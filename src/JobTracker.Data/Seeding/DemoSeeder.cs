using System.Globalization;
using JobTracker.Data.Importing;
using JobTracker.Domain;
using Microsoft.EntityFrameworkCore;

namespace JobTracker.Data.Seeding;

/// <summary>One application from the Phase 0 synthetic file.</summary>
public sealed record SeedRow(int CsvId, string Company, string Title, DateOnly DateApplied, string Status,
    DateOnly? InterviewDate, JobType? JobType = null, string? Location = null, ResumeVariant? ResumeVariant = null,
    ReferralType? ReferralType = null, Priority? Priority = null);

public sealed record SeedResult(int Applications, int Events, int Corrections, int ProblemCount);

/// <summary>
/// Fills the demo database from data/synthetic/fact_application.csv. The CSV has only a current status, so
/// the dates of the events that lead to it are made up here, deterministically from the application's CSV id
/// (never from a random generator), so the same input always gives the same history. All data is synthetic.
/// </summary>
public sealed class DemoSeeder(string requiredDatabase = DemoDatabaseGuard.DemoDatabase)
{
    public const string SeedNote = "synthetic seed";
    public const string CorrectionNote = "synthetic correction";

    public static IReadOnlyList<SeedRow> ReadRows(string csvDir)
    {
        var statusNames = Csv.Parse(File.ReadAllText(Path.Combine(csvDir, "dim_status.csv")))
            .Skip(1).ToDictionary(r => r[0], r => r[1]);

        Dictionary<string, string> Names(string file) =>
            Csv.Parse(File.ReadAllText(Path.Combine(csvDir, file))).Skip(1).ToDictionary(r => r[0], r => r[1]);
        var jobTypeNames = Names("dim_job_type.csv");
        var resumeNames = Names("dim_resume_variant.csv");
        var priorityNames = Names("dim_priority.csv");

        var table = Csv.Parse(File.ReadAllText(Path.Combine(csvDir, "fact_application.csv")));
        var col = table[0].Select((name, i) => (name, i)).ToDictionary(x => x.name, x => x.i);

        var rows = new List<SeedRow>();
        foreach (var r in table.Skip(1))
        {
            var applied = r[col["date_applied"]];
            var status = statusNames[r[col["status_key"]]];
            if (applied == "" || status == "Not Started") continue; // the app needs an applied date

            var interview = r[col["interview_date"]];
            rows.Add(new SeedRow(
                int.Parse(r[col["application_id"]], CultureInfo.InvariantCulture),
                r[col["company_name"]], r[col["job_title"]],
                DateOnly.ParseExact(applied, "yyyy-MM-dd", CultureInfo.InvariantCulture), status,
                interview == "" ? null : DateOnly.ParseExact(interview, "yyyy-MM-dd", CultureInfo.InvariantCulture),
                Lookup(TrackerCsvParser.JobTypes, jobTypeNames[r[col["job_type_key"]]]),
                r[col["location"]] == "" ? null : r[col["location"]],
                Lookup(TrackerCsvParser.ResumeVariants, resumeNames[r[col["resume_variant_key"]]]),
                Lookup(TrackerCsvParser.ReferralTypes, r[col["referral_type"]]),
                Lookup(TrackerCsvParser.Priorities, priorityNames[r[col["priority_key"]]])));
        }

        return rows.OrderBy(r => r.CsvId).ToList();
    }

    /// <summary>Label to enum. An empty label or "Unspecified" means the app stores nothing (null).</summary>
    private static T? Lookup<T>(Dictionary<string, T> labels, string text) where T : struct =>
        text is "" or "Unspecified" ? null : labels[text];

    public SeedResult Seed(JobTrackerDbContext db, IReadOnlyList<SeedRow> rows, DateOnly asOf)
    {
        DemoDatabaseGuard.Require(db.Database.GetConnectionString(), requiredDatabase);

        if (db.Applications.Any())
            throw new InvalidOperationException("The database is not empty. Seeding twice would duplicate history.");

        var clock = new SeedClock();
        var service = new ApplicationService(db, clock);
        var rejectedIndex = 0;

        foreach (var row in rows.OrderBy(r => r.CsvId))
        {
            clock.Set(row.DateApplied);
            var app = service.AddApplication(row.Company, row.Title, row.DateApplied, row.JobType, row.ResumeVariant,
                row.Priority, row.ReferralType, row.Location);
            var mix = Mix(row.CsvId);

            switch (row.Status)
            {
                case "Applied":
                case "No Reply": // No Reply is derived from the Applied event's age, never stored
                    break;

                case "Withdrawn":
                    Record(service, clock, app.Id, ApplicationStatus.Withdrawn, Cap(row.DateApplied.AddDays(1 + mix % 14), asOf));
                    break;

                case "Interviewed":
                    var interview = row.InterviewDate ?? Cap(row.DateApplied.AddDays(7), asOf);
                    var gap = interview.DayNumber - row.DateApplied.DayNumber;
                    Record(service, clock, app.Id, ApplicationStatus.InterviewScheduled, row.DateApplied.AddDays(gap / 2));
                    Record(service, clock, app.Id, ApplicationStatus.Interviewed, interview);
                    break;

                case "Rejected":
                    var date = Cap(row.DateApplied.AddDays(3 + mix % 28), asOf);
                    SeedRejected(service, clock, db, app.Id, row.DateApplied, date, asOf, rejectedIndex++);
                    break;

                default:
                    throw new InvalidOperationException($"Unexpected status '{row.Status}' for {row.Company}.");
            }
        }

        var views = new ApplicationService(db, new SeedClock(asOf)).List();
        var problems = views.Sum(v => v.Projection.Problems.Count);
        if (problems > 0)
            throw new InvalidOperationException($"Seeding produced {problems} projection problem(s). Run with --reset and investigate.");

        return new SeedResult(views.Count, db.StatusEvents.Count(),
            db.StatusEvents.Count(e => e.Source == EventSource.Correction), problems);
    }

    /// <summary>
    /// The first six rejected applications get a small, fixed correction history. Every one ends at Rejected on the
    /// planned date, so the totals do not change. Index 0-2: entered as Withdrawn, corrected. 3-4: wrong date,
    /// corrected. 5: wrong twice (a correction of a correction). Everything else is entered correctly.
    /// </summary>
    private static void SeedRejected(ApplicationService service, SeedClock clock, JobTrackerDbContext db,
        int appId, DateOnly applied, DateOnly date, DateOnly asOf, int index)
    {
        switch (index)
        {
            case <= 2:
                Record(service, clock, appId, ApplicationStatus.Withdrawn, date);
                Correct(service, clock, db, appId, ApplicationStatus.Rejected, date);
                break;
            case 3 or 4:
                var wrongDate = date < asOf ? date.AddDays(1) : (date > applied ? date.AddDays(-1) : date);
                Record(service, clock, appId, ApplicationStatus.Rejected, wrongDate);
                Correct(service, clock, db, appId, ApplicationStatus.Rejected, date);
                break;
            case 5:
                Record(service, clock, appId, ApplicationStatus.Withdrawn, date);
                Correct(service, clock, db, appId, ApplicationStatus.InterviewScheduled, date);
                Correct(service, clock, db, appId, ApplicationStatus.Rejected, date);
                break;
            default:
                Record(service, clock, appId, ApplicationStatus.Rejected, date);
                break;
        }
    }

    private static void Record(ApplicationService service, SeedClock clock, int appId, ApplicationStatus status, DateOnly day)
    {
        clock.Set(day);
        var result = service.RecordStatusChange(appId, status, clock.GetUtcNow(), SeedNote);
        if (!result.Succeeded) throw new InvalidOperationException($"Seed step refused: {result.Error}");
    }

    /// <summary>Corrects the most recent entry of the application.</summary>
    private static void Correct(ApplicationService service, SeedClock clock, JobTrackerDbContext db, int appId,
        ApplicationStatus status, DateOnly day)
    {
        var targetId = db.StatusEvents.Where(e => e.ApplicationId == appId).Max(e => e.Id);
        clock.Set(day, TimeSpan.FromHours(12)); // entered half a day after it happened
        var effective = new DateTimeOffset(day.ToDateTime(TimeOnly.MinValue), TimeSpan.Zero);
        var result = service.CorrectEvent(appId, targetId, status, effective, CorrectionNote);
        if (!result.Succeeded) throw new InvalidOperationException($"Seed correction refused: {result.Error}");
    }

    private static DateOnly Cap(DateOnly day, DateOnly asOf) => day > asOf ? asOf : day;

    /// <summary>A fixed integer scramble of the CSV id. Not random: the same id always gives the same number.</summary>
    private static int Mix(int csvId) => (csvId * 7919 + 104729) % 10007;

    private sealed class SeedClock : TimeProvider
    {
        private DateTimeOffset _now;

        public SeedClock() { }
        public SeedClock(DateOnly day) => Set(day);

        public void Set(DateOnly day, TimeSpan? plus = null) =>
            _now = new DateTimeOffset(day.ToDateTime(TimeOnly.MinValue), TimeSpan.Zero) + (plus ?? TimeSpan.Zero);

        public override DateTimeOffset GetUtcNow() => _now;
    }
}
