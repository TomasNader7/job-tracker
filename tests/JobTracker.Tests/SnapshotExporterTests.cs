using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using JobTracker.Data;
using JobTracker.Data.Seeding;
using JobTracker.Data.Snapshots;
using JobTracker.Domain;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

public class SnapshotFormatTests
{
    private static AppOutcome Out(int id, ApplicationStatus? status, bool noReply = false, int problems = 0,
        string applied = "2026-10-01", string? interview = null) =>
        new(id, status, noReply, problems, DateOnly.Parse(applied, CultureInfo.InvariantCulture),
            interview is null ? null : DateOnly.Parse(interview, CultureInfo.InvariantCulture));

    [Fact]
    public void Gold_application_status_text_is_sorted_by_id_tab_separated_with_nulls_as_backslash_N()
    {
        var text = GoldHash.ApplicationStatusText(
        [
            Out(2, ApplicationStatus.Interviewed, interview: "2026-10-20"),
            Out(1, null, problems: 1),
            Out(3, ApplicationStatus.Applied, noReply: true),
        ]);

        Assert.Equal(
            "1\t\\N\t0\t1\t\\N\n" +
            "2\tInterviewed\t0\t0\t2026-10-20\n" +
            "3\tApplied\t1\t0\t\\N\n", text);
    }

    [Fact]
    public void Hash_is_lowercase_sha256_of_the_utf8_text()
    {
        var expected = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes("abc\n"))).ToLowerInvariant();

        Assert.Equal(expected, GoldHash.Sha256Hex("abc\n"));
    }

    [Fact]
    public void Measures_count_by_the_contract_rules()
    {
        var m = FunnelMeasures.Compute(
        [
            Out(1, ApplicationStatus.Applied),
            Out(2, ApplicationStatus.Applied, noReply: true),
            Out(3, ApplicationStatus.Rejected),
            Out(4, ApplicationStatus.InterviewScheduled),
            Out(5, ApplicationStatus.Interviewed, applied: "2026-10-01", interview: "2026-10-11"),
            Out(6, ApplicationStatus.Accepted, applied: "2026-10-01", interview: "2026-10-04"),
            Out(7, ApplicationStatus.Withdrawn),
            Out(8, ApplicationStatus.NotStarted),   // not an application
            Out(9, null),                            // no valid history, not an application
        ]);

        Assert.Equal(7, m.Applications);
        Assert.Equal(4, m.Responses);     // Rejected, InterviewScheduled, Interviewed, Accepted
        Assert.Equal(3, m.Interviews);    // InterviewScheduled, Interviewed, Accepted
        Assert.Equal(1, m.Offers);
        Assert.Equal(1, m.Rejections);
        Assert.Equal(1, m.NoReplies);
        Assert.Equal(2, m.RowsWithInterviewDate);
        Assert.Equal(6.50m, m.AvgDaysToInterview);   // (10 + 3) / 2
    }

    [Fact]
    public void Rates_round_half_up_to_4_places_and_average_to_2()
    {
        // 1/8 = 0.125 exactly; 1/16 = 0.0625 exactly; half-up keeps the tie at 4 places: 0.0625.
        // 1/32 = 0.03125 is a tie at 4 places: half-up gives 0.0313 (banker's rounding would give 0.0312).
        var apps = Enumerable.Range(1, 32).Select(i => Out(i, i == 1 ? ApplicationStatus.Rejected : ApplicationStatus.Applied)).ToList();

        var m = FunnelMeasures.Compute(apps);

        Assert.Equal(0.0313m, m.RejectionsRate);
        Assert.Equal("0.0313", m.RejectionsRate!.Value.ToString("F4", CultureInfo.InvariantCulture));
    }

    [Fact]
    public void Average_days_ties_round_half_up_to_2_places()
    {
        // days 1 and 2 and 2 => 5/3 = 1.6667; days 1,1,1,2 => 1.25 exactly; days sum 1/8 tie: 0.125 -> 0.13
        var apps = new[]
        {
            Out(1, ApplicationStatus.Interviewed, applied: "2026-10-01", interview: "2026-10-01"),
            Out(2, ApplicationStatus.Interviewed, applied: "2026-10-01", interview: "2026-10-01"),
            Out(3, ApplicationStatus.Interviewed, applied: "2026-10-01", interview: "2026-10-01"),
            Out(4, ApplicationStatus.Interviewed, applied: "2026-10-01", interview: "2026-10-01"),
            Out(5, ApplicationStatus.Interviewed, applied: "2026-10-01", interview: "2026-10-01"),
            Out(6, ApplicationStatus.Interviewed, applied: "2026-10-01", interview: "2026-10-01"),
            Out(7, ApplicationStatus.Interviewed, applied: "2026-10-01", interview: "2026-10-01"),
            Out(8, ApplicationStatus.Interviewed, applied: "2026-10-01", interview: "2026-10-02"),
        };

        Assert.Equal(0.13m, FunnelMeasures.Compute(apps).AvgDaysToInterview);   // 1/8 = 0.125
    }

    [Fact]
    public void Zero_applications_gives_null_rates_and_average()
    {
        var m = FunnelMeasures.Compute([Out(1, ApplicationStatus.NotStarted)]);

        Assert.Equal(0, m.Applications);
        Assert.Null(m.ResponsesRate);
        Assert.Null(m.AvgDaysToInterview);
        Assert.Equal("0\t0\t0\t0\t0\t0\t0\t\\N\t\\N\t\\N\t\\N\t\\N\t\\N\n", m.HashText());
    }

    [Fact]
    public void Funnel_hash_text_uses_fixed_decimals_in_the_contract_order()
    {
        var m = FunnelMeasures.Compute(
        [
            Out(1, ApplicationStatus.Interviewed, applied: "2026-10-01", interview: "2026-10-22"),
            Out(2, ApplicationStatus.Applied),
        ]);

        // Applications, Responses, Interviews, Offers, Rejections, No_Replies, Rows_with_interview_date,
        // Avg_Days_to_Interview, Responses_Rate, Interviews_Rate, Offers_Rate, Rejections_Rate, No_Replies_Rate
        Assert.Equal("2\t1\t1\t0\t0\t0\t1\t21.00\t0.5000\t0.5000\t0.0000\t0.0000\t0.0000\n", m.HashText());
    }

    [Theory]
    [InlineData("2026-11-16T00:00:00.000000Z", "2026-11-16T00:00:00.000000Z")]
    [InlineData("2026-11-16T05:30:15.123456+05:30", "2026-11-16T00:00:15.123456Z")]
    public void Timestamps_are_utc_with_six_fractional_digits(string input, string expected)
    {
        Assert.Equal(expected, SnapshotFormat.Timestamp(DateTimeOffset.Parse(input, CultureInfo.InvariantCulture)));
    }

    [Fact]
    public void A_timestamp_finer_than_a_microsecond_is_not_representable()
    {
        var fine = new DateTimeOffset(2026, 11, 16, 0, 0, 0, TimeSpan.Zero).AddTicks(7);

        Assert.False(SnapshotFormat.IsRepresentable(fine));
        Assert.True(SnapshotFormat.IsRepresentable(fine.AddTicks(3)));   // 10 ticks = 1 microsecond
    }

    [Theory]
    [InlineData("snap-0123456789ab", true)]
    [InlineData("demo-2026-11-16", true)]
    [InlineData("abc", true)]
    [InlineData("ab", false)]
    [InlineData("Snap-1", false)]
    [InlineData("../evil", false)]
    [InlineData("a/b", false)]
    [InlineData("a b c", false)]
    [InlineData("-leading", false)]
    [InlineData("latest ", false)]
    [InlineData("", false)]
    public void Snapshot_ids_must_be_safe(string id, bool valid)
    {
        Assert.Equal(valid, SnapshotFormat.IsSafeSnapshotId(id));
    }
}

/// <summary>Uses the throwaway database JobTracker_Test_Export.</summary>
public sealed class SnapshotExporterTests : IDisposable
{
    private const string Db = "JobTracker_Test_Export";
    private static readonly DateOnly AsOfDay = new(2026, 11, 16);
    private static readonly DateTimeOffset AsOf = new(2026, 11, 16, 0, 0, 0, TimeSpan.Zero);
    private static readonly DateTimeOffset ExportedAt = new(2026, 11, 17, 9, 30, 0, TimeSpan.Zero);
    private readonly List<string> _tempDirs = [];

    public SnapshotExporterTests()
    {
        using var db = NewContext();
        db.Database.EnsureDeleted();
        db.Database.Migrate();
    }

    public void Dispose()
    {
        using (var db = NewContext()) db.Database.EnsureDeleted();
        foreach (var d in _tempDirs.Where(Directory.Exists)) Directory.Delete(d, true);
    }

    private static JobTrackerDbContext NewContext() =>
        new(new DbContextOptionsBuilder<JobTrackerDbContext>()
            .UseSqlServer($@"Server=(localdb)\MSSQLLocalDB;Database={Db};Trusted_Connection=True;")
            .Options);

    private string TempRoot()
    {
        var dir = Path.Combine(Path.GetTempPath(), "jt-snap-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        _tempDirs.Add(dir);
        return dir;
    }

    private static void Seed()
    {
        using var db = NewContext();
        new DemoSeeder(Db).Seed(db, DemoSeeder.ReadRows(RepoPaths.SyntheticDataDir), AsOfDay);
    }

    private static ExportResult Export(string root, DateTimeOffset? exportedAt = null, string? id = null)
    {
        using var db = NewContext();
        return new SnapshotExporter(Db).Export(db, root, new ExportOptions(AsOf, exportedAt ?? ExportedAt, id));
    }

    private static JsonElement Manifest(ExportResult r) =>
        JsonDocument.Parse(File.ReadAllText(Path.Combine(r.Folder, "manifest.json"))).RootElement;

    [Fact]
    public void Applications_csv_carries_the_five_optional_fields_as_enum_names_with_empty_for_null()
    {
        Seed();
        var r = Export(TempRoot());

        var rows = File.ReadAllLines(Path.Combine(r.Folder, "applications.csv"));
        // Company 009: job type 7, resume variant 5, priority 2, Cold Apply, "Austin, TX" (quoted: it has a comma).
        var row = rows.Single(l => l.Contains(",Company 009,"));
        Assert.Contains(",SoftwareEngineer,DataEngineering,MediumPriority,ColdApply,\"Austin, TX\",", row);

        // "Unspecified" priority is stored as null, so it is an empty field (priority is column 7).
        var table = Csv.Parse(File.ReadAllText(Path.Combine(r.Folder, "applications.csv")));
        Assert.Equal(10, table.Skip(1).Count(f => f[6] == ""));
    }

    [Fact]
    public void Writes_exactly_the_three_allowlisted_files()
    {
        Seed();

        var r = Export(TempRoot());

        var names = Directory.GetFiles(r.Folder).Select(Path.GetFileName).Order().ToArray();
        Assert.Equal(new[] { "applications.csv", "manifest.json", "status_events.csv" }, names);
        Assert.Empty(Directory.GetDirectories(r.Folder));
        Assert.Equal(r.SnapshotId, Path.GetFileName(r.Folder));
    }

    [Fact]
    public void Csv_files_are_utf8_without_bom_lf_only_with_the_contract_headers()
    {
        Seed();
        var r = Export(TempRoot());

        foreach (var file in new[] { "applications.csv", "status_events.csv" })
        {
            var bytes = File.ReadAllBytes(Path.Combine(r.Folder, file));
            Assert.False(bytes.Length >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF, "BOM");
            Assert.DoesNotContain((byte)'\r', bytes);
            Assert.Equal((byte)'\n', bytes[^1]);
        }

        var apps = File.ReadAllLines(Path.Combine(r.Folder, "applications.csv"));
        var events = File.ReadAllLines(Path.Combine(r.Folder, "status_events.csv"));
        Assert.Equal("application_id,company_name,job_title,date_applied,job_type,resume_variant,priority,referral_type,location,expected_projected_status,expected_is_no_reply", apps[0]);
        Assert.Equal("event_sequence,application_id,previous_status,new_status,effective_at,recorded_at,source,supersedes_event_sequence,note", events[0]);
        Assert.Equal(183, apps.Length);   // header + 182
    }

    [Fact]
    public void Rows_are_sorted_by_key_and_event_sequence_is_dense_from_1()
    {
        Seed();
        var r = Export(TempRoot());

        var appIds = File.ReadAllLines(Path.Combine(r.Folder, "applications.csv")).Skip(1)
            .Select(l => int.Parse(l.Split(',')[0], CultureInfo.InvariantCulture)).ToList();
        var seqs = File.ReadAllLines(Path.Combine(r.Folder, "status_events.csv")).Skip(1)
            .Select(l => long.Parse(l.Split(',')[0], CultureInfo.InvariantCulture)).ToList();

        Assert.Equal(appIds.Order(), appIds);
        Assert.Equal(Enumerable.Range(1, seqs.Count).Select(i => (long)i), seqs);
    }

    [Fact]
    public void Corrections_point_at_the_event_sequence_of_their_target()
    {
        Seed();
        var r = Export(TempRoot());

        var rows = Csv.Parse(File.ReadAllText(Path.Combine(r.Folder, "status_events.csv"))).Skip(1).ToList();
        var bySeq = rows.ToDictionary(x => x[0]);
        var corrections = rows.Where(x => x[6] == "Correction").ToList();

        Assert.Equal(7, corrections.Count);
        foreach (var c in corrections)
        {
            Assert.NotEqual("", c[7]);
            Assert.True(bySeq.ContainsKey(c[7]), "target exists");
            Assert.Equal(c[1], bySeq[c[7]][1]);   // same application
            Assert.True(long.Parse(c[7], CultureInfo.InvariantCulture) < long.Parse(c[0], CultureInfo.InvariantCulture));
        }
        Assert.All(rows.Where(x => x[6] != "Correction"), x => Assert.Equal("", x[7]));
    }

    [Fact]
    public void Job_titles_with_commas_are_quoted()
    {
        Seed();
        var r = Export(TempRoot());

        var text = File.ReadAllText(Path.Combine(r.Folder, "applications.csv"));

        Assert.Contains("\"Data Scientist, New Grad\"", text);
    }

    [Fact]
    public void Expected_measures_equal_the_phase_0_expected_measures_json()
    {
        Seed();
        var r = Export(TempRoot());

        var expected = JsonDocument.Parse(File.ReadAllText(Path.Combine(RepoPaths.SyntheticDataDir, "expected_measures.json"))).RootElement;
        var m = Manifest(r).GetProperty("expected_measures");

        decimal Dec(JsonElement e) => decimal.Parse(e.GetString()!, CultureInfo.InvariantCulture);

        Assert.Equal(expected.GetProperty("Applications").GetInt32(), m.GetProperty("Applications").GetInt32());
        Assert.Equal(expected.GetProperty("Responses").GetInt32(), m.GetProperty("Responses").GetInt32());
        Assert.Equal(expected.GetProperty("Interviews").GetInt32(), m.GetProperty("Interviews").GetInt32());
        Assert.Equal(expected.GetProperty("Offers").GetInt32(), m.GetProperty("Offers").GetInt32());
        Assert.Equal(expected.GetProperty("Rejections").GetInt32(), m.GetProperty("Rejections").GetInt32());
        Assert.Equal(expected.GetProperty("No Replies").GetInt32(), m.GetProperty("No_Replies").GetInt32());
        Assert.Equal(expected.GetProperty("Rows with interview date").GetInt32(), m.GetProperty("Rows_with_interview_date").GetInt32());
        Assert.Equal(expected.GetProperty("Avg Days to Interview").GetDecimal(), Dec(m.GetProperty("Avg_Days_to_Interview")));
        Assert.Equal(expected.GetProperty("Response Rate").GetDecimal(), Dec(m.GetProperty("Responses_Rate")));
        Assert.Equal(expected.GetProperty("Interview Rate").GetDecimal(), Dec(m.GetProperty("Interviews_Rate")));
        Assert.Equal(expected.GetProperty("Offer Rate").GetDecimal(), Dec(m.GetProperty("Offers_Rate")));
        Assert.Equal(expected.GetProperty("Rejection Rate").GetDecimal(), Dec(m.GetProperty("Rejections_Rate")));
        Assert.Equal(expected.GetProperty("No-Reply Rate").GetDecimal(), Dec(m.GetProperty("No_Replies_Rate")));
    }

    [Fact]
    public void Manifest_carries_the_dimension_funnel_hash_and_row_count()
    {
        Seed();
        var r = Export(TempRoot());
        var gold = Manifest(r).GetProperty("gold_hash");

        var hash = gold.GetProperty("gold_funnel_by_dimension").GetString()!;
        Assert.Matches("^[0-9a-f]{64}$", hash);
        // 9 job types + 7 resume variants + 4 priorities + 2 referral types, as far as the 182 synthetic rows use them.
        var rows = gold.GetProperty("gold_funnel_by_dimension_rows").GetInt32();
        Assert.InRange(rows, 15, 22);
    }

    [Fact]
    public void Manifest_describes_the_files_it_ships_with()
    {
        Seed();
        var r = Export(TempRoot());
        var m = Manifest(r);

        Assert.Equal("2", m.GetProperty("contract_version").GetString());
        Assert.Equal("synthetic", m.GetProperty("lane").GetString());
        Assert.Equal(r.SnapshotId, m.GetProperty("snapshot_id").GetString());
        Assert.Equal("2026-11-16T00:00:00.000000Z", m.GetProperty("as_of").GetString());
        Assert.Equal("2026-11-17T09:30:00.000000Z", m.GetProperty("exported_at").GetString());

        foreach (var f in m.GetProperty("files").EnumerateArray())
        {
            var path = Path.Combine(r.Folder, f.GetProperty("name").GetString()!);
            var sha = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
            Assert.Equal(sha, f.GetProperty("sha256").GetString());
            Assert.Equal(File.ReadAllLines(path).Length - 1, f.GetProperty("rows").GetInt32());
            Assert.True(f.GetProperty("columns").GetArrayLength() >= 6);
        }

        Assert.Equal(182, m.GetProperty("applications").GetProperty("accepted").GetInt32());
        Assert.Equal(0, m.GetProperty("applications").GetProperty("rejected").GetInt32());
        Assert.Equal(0, m.GetProperty("expected_problems").GetProperty("total").GetInt32());
        Assert.Equal(64, m.GetProperty("gold_hash").GetProperty("gold_application_status").GetString()!.Length);
        Assert.Equal(64, m.GetProperty("gold_hash").GetProperty("gold_funnel_summary").GetString()!.Length);
    }

    [Fact]
    public void Same_state_and_as_of_give_byte_identical_data_files_and_snapshot_id()
    {
        Seed();

        var a = Export(TempRoot());
        var b = Export(TempRoot());

        Assert.Equal(a.SnapshotId, b.SnapshotId);
        foreach (var file in new[] { "applications.csv", "status_events.csv", "manifest.json" })
            Assert.Equal(File.ReadAllBytes(Path.Combine(a.Folder, file)), File.ReadAllBytes(Path.Combine(b.Folder, file)));
    }

    [Fact]
    public void The_demo_snapshot_at_the_pinned_as_of_matches_the_recorded_values()
    {
        // Regression pin. If the seeder, exporter or contract changes on purpose, update these values AND re-run
        // the Databricks reconciliation, because the pipeline must reproduce them independently.
        Seed();

        var r = Export(TempRoot());
        var m = Manifest(r);
        var hashes = m.GetProperty("files").EnumerateArray().ToDictionary(f => f.GetProperty("name").GetString()!, f => f.GetProperty("sha256").GetString()!);

        Assert.Equal("snap-4f27c803340e", r.SnapshotId);
        Assert.Equal("46a44e4f60e2615eaacbc534f4019d08d3afe78f47ec7bf0957a3c702f74667c", hashes["applications.csv"]);
        Assert.Equal("670efb86bdd6d99dfb56336b56b9dfbb7a49110b372b8805e154b5500e1708b8", hashes["status_events.csv"]);
        Assert.Equal("8a0349c26a2161328fa825fb13e5fb7970a7193726abadd4230f26ffd7680336",
            m.GetProperty("gold_hash").GetProperty("gold_application_status").GetString());
        Assert.Equal("e7baa2af9f99c330a4f066cc398a124070e2e9ee19de50d0f726ec1d5a73e1e5",
            m.GetProperty("gold_hash").GetProperty("gold_funnel_summary").GetString());
        Assert.Equal(248, m.GetProperty("files")[1].GetProperty("rows").GetInt32());
    }

    [Fact]
    public void A_different_exported_at_changes_only_the_manifest_field_exported_at()
    {
        Seed();

        var a = Export(TempRoot(), ExportedAt);
        var b = Export(TempRoot(), ExportedAt.AddHours(5));

        Assert.Equal(a.SnapshotId, b.SnapshotId);
        foreach (var file in new[] { "applications.csv", "status_events.csv" })
            Assert.Equal(File.ReadAllBytes(Path.Combine(a.Folder, file)), File.ReadAllBytes(Path.Combine(b.Folder, file)));

        var ma = File.ReadAllText(Path.Combine(a.Folder, "manifest.json")).Replace("09:30:00", "XX");
        var mb = File.ReadAllText(Path.Combine(b.Folder, "manifest.json")).Replace("14:30:00", "XX");
        Assert.Equal(ma, mb);
    }

    [Fact]
    public void A_different_as_of_gives_a_different_snapshot_id()
    {
        Seed();
        using var db = NewContext();
        var exporter = new SnapshotExporter(Db);

        var a = exporter.Export(db, TempRoot(), new ExportOptions(AsOf, ExportedAt, null));
        var b = exporter.Export(db, TempRoot(), new ExportOptions(AsOf.AddDays(40), ExportedAt, null));

        Assert.NotEqual(a.SnapshotId, b.SnapshotId);
    }

    [Fact]
    public void Derived_snapshot_id_follows_the_contract_formula()
    {
        Seed();
        var r = Export(TempRoot());
        var m = Manifest(r);
        var hashes = m.GetProperty("files").EnumerateArray().ToDictionary(f => f.GetProperty("name").GetString()!, f => f.GetProperty("sha256").GetString()!);

        var text = $"2\n2026-11-16T00:00:00.000000Z\n{hashes["applications.csv"]}\n{hashes["status_events.csv"]}";
        var expected = "snap-" + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(text))).ToLowerInvariant()[..12];

        Assert.Equal(expected, r.SnapshotId);
        Assert.True(SnapshotFormat.IsSafeSnapshotId(r.SnapshotId));
    }

    [Fact]
    public void A_supplied_snapshot_id_is_used_when_it_is_safe()
    {
        Seed();

        var r = Export(TempRoot(), id: "demo-2026-11-16");

        Assert.Equal("demo-2026-11-16", r.SnapshotId);
        Assert.Equal("demo-2026-11-16", Manifest(r).GetProperty("snapshot_id").GetString());
    }

    [Theory]
    [InlineData("../evil")]
    [InlineData("Snap-1")]
    [InlineData("ab")]
    [InlineData("a/b")]
    public void An_unsafe_supplied_snapshot_id_is_refused_and_nothing_is_written(string id)
    {
        Seed();
        var root = TempRoot();

        Assert.Throws<ArgumentException>(() => Export(root, id: id));

        Assert.Empty(Directory.GetFileSystemEntries(root));
    }

    [Fact]
    public void Refuses_a_database_that_is_not_the_demo_database_and_writes_nothing()
    {
        Seed();
        var root = TempRoot();
        using var db = NewContext();

        // The default exporter allows only JobTracker_Demo; this is a test database.
        Assert.Throws<DemoGuardException>(() =>
            new SnapshotExporter().Export(db, root, new ExportOptions(AsOf, ExportedAt, null)));

        Assert.Empty(Directory.GetFileSystemEntries(root));
    }

    [Fact]
    public void Exporting_again_into_the_same_folder_is_harmless_when_the_data_is_identical()
    {
        Seed();
        var root = TempRoot();

        var first = Export(root);
        var second = Export(root, ExportedAt.AddHours(1));

        Assert.Equal(first.SnapshotId, second.SnapshotId);
        Assert.Single(Directory.GetDirectories(root));
        Assert.Contains("09:30:00", File.ReadAllText(Path.Combine(first.Folder, "manifest.json")));   // original kept
    }

    [Fact]
    public void Refuses_to_overwrite_a_different_snapshot_that_has_the_same_id()
    {
        Seed();
        var root = TempRoot();
        var existing = Path.Combine(root, "demo-fixed");
        Directory.CreateDirectory(existing);
        File.WriteAllText(Path.Combine(existing, "applications.csv"), "something else\n");
        File.WriteAllText(Path.Combine(existing, "status_events.csv"), "something else\n");
        File.WriteAllText(Path.Combine(existing, "manifest.json"), "{}\n");

        var ex = Assert.Throws<InvalidOperationException>(() => Export(root, id: "demo-fixed"));

        Assert.Contains("overwrite", ex.Message);
        Assert.Equal("something else\n", File.ReadAllText(Path.Combine(existing, "applications.csv")));
    }

    [Fact]
    public void Application_without_events_or_with_a_timestamp_finer_than_a_microsecond_is_rejected_not_exported()
    {
        Seed();
        using (var db = NewContext())
        {
            var noEvents = new Application { CompanyName = "Company 900", JobTitle = "Data Analyst", DateApplied = new DateOnly(2026, 11, 1) };
            var fine = new Application { CompanyName = "Company 901", JobTitle = "Data Analyst", DateApplied = new DateOnly(2026, 11, 1) };
            db.Applications.AddRange(noEvents, fine);
            db.SaveChanges();
            var t = new DateTimeOffset(2026, 11, 1, 0, 0, 0, TimeSpan.Zero).AddTicks(7);
            db.StatusEvents.Add(StatusEvent.Create(0, fine.Id, null, ApplicationStatus.Applied, t, t, EventSource.Manual));
            db.SaveChanges();
        }

        var r = Export(TempRoot());
        var apps = Manifest(r).GetProperty("applications");

        Assert.Equal(182, apps.GetProperty("accepted").GetInt32());
        Assert.Equal(2, apps.GetProperty("rejected").GetInt32());
        Assert.Equal(1, apps.GetProperty("rejected_reasons").GetProperty("NO_EVENTS").GetInt32());
        Assert.Equal(1, apps.GetProperty("rejected_reasons").GetProperty("TIMESTAMP_PRECISION").GetInt32());
        var text = File.ReadAllText(Path.Combine(r.Folder, "applications.csv"));
        Assert.DoesNotContain("Company 900", text);
        Assert.DoesNotContain("Company 901", text);
    }

    [Fact]
    public void An_application_with_projection_problems_is_exported_and_counted_by_code()
    {
        Seed();
        int id;
        using (var db = NewContext())
        {
            var app = new Application { CompanyName = "Company 902", JobTitle = "Data Analyst", DateApplied = new DateOnly(2026, 11, 1) };
            db.Applications.Add(app);
            db.SaveChanges();
            id = app.Id;
            var d1 = new DateTimeOffset(2026, 11, 1, 0, 0, 0, TimeSpan.Zero);
            db.StatusEvents.Add(StatusEvent.Create(0, id, null, ApplicationStatus.Applied, d1, d1, EventSource.Import));
            db.StatusEvents.Add(StatusEvent.Create(0, id, ApplicationStatus.Applied, ApplicationStatus.Accepted, d1.AddDays(3), d1.AddDays(3), EventSource.Import));
            db.SaveChanges();
        }

        var r = Export(TempRoot());
        var m = Manifest(r);

        Assert.Equal(183, m.GetProperty("applications").GetProperty("accepted").GetInt32());
        Assert.Equal(1, m.GetProperty("expected_problems").GetProperty("total").GetInt32());
        Assert.Equal(1, m.GetProperty("expected_problems").GetProperty("by_code").GetProperty("ILLEGAL_TRANSITION").GetInt32());
        Assert.Equal(0, m.GetProperty("expected_problems").GetProperty("by_code").GetProperty("ILLEGAL_FIRST").GetInt32());
        var line = File.ReadAllLines(Path.Combine(r.Folder, "applications.csv")).Single(l => l.Contains("Company 902"));
        Assert.EndsWith(",Applied,0", line);   // chain stopped at the last valid status
    }
}
