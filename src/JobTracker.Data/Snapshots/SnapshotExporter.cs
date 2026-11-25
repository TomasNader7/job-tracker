using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using JobTracker.Data.Seeding;
using JobTracker.Domain;
using Microsoft.EntityFrameworkCore;

namespace JobTracker.Data.Snapshots;

/// <param name="AsOf">When statuses are computed. Pin it for a reproducible snapshot.</param>
/// <param name="ExportedAt">When the files were written. Operational only; outside every hash and the snapshot id.</param>
/// <param name="SnapshotId">Optional. Derived from the content when null.</param>
public sealed record ExportOptions(DateTimeOffset AsOf, DateTimeOffset ExportedAt, string? SnapshotId);

public sealed record ExportResult(string SnapshotId, string Folder, bool AlreadyExisted, FunnelMeasures Measures,
    int Applications, int Events);

/// <summary>
/// Writes a versioned snapshot (snapshot contract v2) of the demo database: applications.csv, status_events.csv and
/// manifest.json. The expected values in the manifest come from the C# projection; the Databricks pipeline must
/// reach the same numbers on its own. Refuses every database except JobTracker_Demo.
/// </summary>
public sealed class SnapshotExporter(string requiredDatabase = DemoDatabaseGuard.DemoDatabase)
{
    private static readonly UTF8Encoding Utf8 = new(false);

    private static readonly string[] AllCodes =
        ["ILLEGAL_FIRST", "FIRST_PREVIOUS_NOT_NULL", "ILLEGAL_TRANSITION", "PREVIOUS_MISMATCH", "CORRECTION_TARGET_INVALID"];

    public ExportResult Export(JobTrackerDbContext db, string outputRoot, ExportOptions options)
    {
        DemoDatabaseGuard.Require(db.Database.GetConnectionString(), requiredDatabase);

        if (options.SnapshotId is not null && !SnapshotFormat.IsSafeSnapshotId(options.SnapshotId))
            throw new ArgumentException($"Unsafe snapshot id '{options.SnapshotId}'. Use 3-64 characters: a-z, 0-9 and '-', not starting with '-'.", nameof(options));
        if (!SnapshotFormat.IsRepresentable(options.AsOf) || !SnapshotFormat.IsRepresentable(options.ExportedAt))
            throw new ArgumentException("as-of and exported-at must be whole microseconds.", nameof(options));

        var apps = db.Applications.AsNoTracking().OrderBy(a => a.Id).ToList();
        var eventsByApp = db.StatusEvents.AsNoTracking().OrderBy(e => e.Id).ToList()
            .GroupBy(e => e.ApplicationId).ToDictionary(g => g.Key, g => g.ToList());

        // Applications that cannot be exported cleanly are counted and left out, never silently altered.
        var accepted = new List<(Application App, List<StatusEvent> Events)>();
        int noEvents = 0, badPrecision = 0;
        foreach (var app in apps)
        {
            if (!eventsByApp.TryGetValue(app.Id, out var events) || events.Count == 0) { noEvents++; continue; }
            if (events.Any(e => !SnapshotFormat.IsRepresentable(e.EffectiveAt) || !SnapshotFormat.IsRepresentable(e.RecordedAt)))
            { badPrecision++; continue; }
            accepted.Add((app, events));
        }

        // event_sequence: dense 1..N in the same order as the event Id, so the contract tie-break equals the app's.
        var acceptedEvents = accepted.SelectMany(a => a.Events).OrderBy(e => e.Id).ToList();
        var sequenceOf = acceptedEvents.Select((e, i) => (e.Id, Seq: (long)i + 1)).ToDictionary(x => x.Id, x => x.Seq);

        var outcomes = new List<AppOutcome>();
        var problemCounts = AllCodes.ToDictionary(c => c, _ => 0);
        var applicationLines = new StringBuilder(Csv.Line(SnapshotFormat.ApplicationColumns)).Append('\n');
        foreach (var (app, events) in accepted)
        {
            var result = StatusProjection.Project(events, options.AsOf);
            foreach (var p in result.ProblemDetails) problemCounts[p.ContractCode]++;

            outcomes.Add(new AppOutcome(app.Id, result.Status, result.IsNoReply, result.ProblemDetails.Count, app.DateApplied,
                result.FirstInterviewedAt is { } t ? DateOnly.FromDateTime(t.UtcDateTime) : null));

            applicationLines.Append(Csv.Line(
            [
                app.Id.ToString(CultureInfo.InvariantCulture), app.CompanyName, app.JobTitle,
                app.DateApplied.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                app.JobType?.ToString(), app.ResumeVariant?.ToString(), app.Priority?.ToString(),
                app.ReferralType?.ToString(), app.Location,
                result.Status?.ToString(), result.IsNoReply ? "1" : "0",
            ])).Append('\n');
        }

        var eventLines = new StringBuilder(Csv.Line(SnapshotFormat.EventColumns)).Append('\n');
        foreach (var e in acceptedEvents)
        {
            eventLines.Append(Csv.Line(
            [
                sequenceOf[e.Id].ToString(CultureInfo.InvariantCulture),
                e.ApplicationId.ToString(CultureInfo.InvariantCulture),
                e.PreviousStatus?.ToString(), e.NewStatus.ToString(),
                SnapshotFormat.Timestamp(e.EffectiveAt), SnapshotFormat.Timestamp(e.RecordedAt),
                e.Source.ToString(),
                e.SupersedesEventId is { } s ? sequenceOf[s].ToString(CultureInfo.InvariantCulture) : null,
                e.Note,
            ])).Append('\n');
        }

        var applicationsBytes = Utf8.GetBytes(applicationLines.ToString());
        var eventsBytes = Utf8.GetBytes(eventLines.ToString());
        var applicationsHash = Sha256Hex(applicationsBytes);
        var eventsHash = Sha256Hex(eventsBytes);
        var asOfText = SnapshotFormat.Timestamp(options.AsOf);

        var snapshotId = options.SnapshotId ?? "snap-" + GoldHash.Sha256Hex(
            $"{SnapshotFormat.ContractVersion}\n{asOfText}\n{applicationsHash}\n{eventsHash}")[..12];

        var measures = FunnelMeasures.Compute(outcomes);
        var manifest = BuildManifest(snapshotId, asOfText, SnapshotFormat.Timestamp(options.ExportedAt),
            applicationsHash, eventsHash, accepted.Count, acceptedEvents.Count, noEvents, badPrecision, measures,
            problemCounts, GoldHash.Sha256Hex(GoldHash.ApplicationStatusText(outcomes)), GoldHash.Sha256Hex(measures.HashText()));

        var folder = Write(outputRoot, snapshotId, applicationsBytes, eventsBytes, manifest, out var alreadyExisted);
        return new ExportResult(snapshotId, folder, alreadyExisted, measures, accepted.Count, acceptedEvents.Count);
    }

    private static byte[] BuildManifest(string snapshotId, string asOf, string exportedAt, string applicationsHash,
        string eventsHash, int applicationRows, int eventRows, int noEvents, int badPrecision, FunnelMeasures m,
        Dictionary<string, int> problemCounts, string goldStatusHash, string goldFunnelHash)
    {
        static List<object> Columns(string[] names, string[] types) =>
            names.Select((n, i) => (object)new Dictionary<string, object?> { ["name"] = n, ["type"] = types[i] }).ToList();

        static string? Fixed(decimal? v, string format) => v?.ToString(format, CultureInfo.InvariantCulture);

        var manifest = new Dictionary<string, object?>
        {
            ["contract_version"] = SnapshotFormat.ContractVersion,
            ["lane"] = SnapshotFormat.Lane,
            ["snapshot_id"] = snapshotId,
            ["as_of"] = asOf,
            ["exported_at"] = exportedAt,
            ["files"] = new List<object>
            {
                new Dictionary<string, object?>
                {
                    ["name"] = "applications.csv", ["columns"] = Columns(SnapshotFormat.ApplicationColumns, SnapshotFormat.ApplicationColumnTypes),
                    ["rows"] = applicationRows, ["sha256"] = applicationsHash,
                },
                new Dictionary<string, object?>
                {
                    ["name"] = "status_events.csv", ["columns"] = Columns(SnapshotFormat.EventColumns, SnapshotFormat.EventColumnTypes),
                    ["rows"] = eventRows, ["sha256"] = eventsHash,
                },
            },
            ["applications"] = new Dictionary<string, object?>
            {
                ["accepted"] = applicationRows,
                ["rejected"] = noEvents + badPrecision,
                ["rejected_reasons"] = new Dictionary<string, object?> { ["NO_EVENTS"] = noEvents, ["TIMESTAMP_PRECISION"] = badPrecision },
            },
            // Counts are numbers. Rates and the average are fixed-decimal STRINGS so no reader can turn them into floats.
            ["expected_measures"] = new Dictionary<string, object?>
            {
                ["Applications"] = m.Applications, ["Responses"] = m.Responses, ["Interviews"] = m.Interviews,
                ["Offers"] = m.Offers, ["Rejections"] = m.Rejections, ["No_Replies"] = m.NoReplies,
                ["Rows_with_interview_date"] = m.RowsWithInterviewDate,
                ["Avg_Days_to_Interview"] = Fixed(m.AvgDaysToInterview, "F2"),
                ["Responses_Rate"] = Fixed(m.ResponsesRate, "F4"), ["Interviews_Rate"] = Fixed(m.InterviewsRate, "F4"),
                ["Offers_Rate"] = Fixed(m.OffersRate, "F4"), ["Rejections_Rate"] = Fixed(m.RejectionsRate, "F4"),
                ["No_Replies_Rate"] = Fixed(m.NoRepliesRate, "F4"),
            },
            ["expected_problems"] = new Dictionary<string, object?>
            {
                ["total"] = problemCounts.Values.Sum(),
                ["by_code"] = AllCodes.ToDictionary(c => c, c => (object?)problemCounts[c]),
            },
            ["gold_hash"] = new Dictionary<string, object?>
            {
                ["algorithm"] = "sha256",
                ["definition"] = "docs/snapshot-contract.md, section Canonical gold-content hash",
                ["gold_application_status"] = goldStatusHash,
                ["gold_funnel_summary"] = goldFunnelHash,
            },
        };

        var options = new JsonSerializerOptions { WriteIndented = true, NewLine = "\n" };
        return Utf8.GetBytes(JsonSerializer.Serialize(manifest, options) + "\n");
    }

    /// <summary>Writes the snapshot folder. Refuses to replace a different snapshot with the same id.</summary>
    private static string Write(string outputRoot, string snapshotId, byte[] applications, byte[] events, byte[] manifest,
        out bool alreadyExisted)
    {
        Directory.CreateDirectory(outputRoot);
        var target = Path.Combine(outputRoot, snapshotId);
        alreadyExisted = false;

        if (Directory.Exists(target))
        {
            var files = Directory.GetFiles(target).Select(Path.GetFileName).Order().ToArray();
            var same = files.SequenceEqual(new[] { "applications.csv", "manifest.json", "status_events.csv" })
                       && Directory.GetDirectories(target).Length == 0
                       && File.ReadAllBytes(Path.Combine(target, "applications.csv")).AsSpan().SequenceEqual(applications)
                       && File.ReadAllBytes(Path.Combine(target, "status_events.csv")).AsSpan().SequenceEqual(events);
            if (!same)
                throw new InvalidOperationException($"Refusing to overwrite the existing, different snapshot '{snapshotId}'.");

            alreadyExisted = true; // identical data: keep what is on disk, including its original exported_at
            return target;
        }

        var temp = Path.Combine(outputRoot, ".tmp-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(temp);
        try
        {
            File.WriteAllBytes(Path.Combine(temp, "applications.csv"), applications);
            File.WriteAllBytes(Path.Combine(temp, "status_events.csv"), events);
            File.WriteAllBytes(Path.Combine(temp, "manifest.json"), manifest);
            Directory.Move(temp, target);
        }
        catch
        {
            if (Directory.Exists(temp)) Directory.Delete(temp, true);
            throw;
        }

        return target;
    }

    private static string Sha256Hex(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
}
