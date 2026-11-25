using JobTracker.Data;
using JobTracker.Data.Importing;
using JobTracker.Domain;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Importer piece 3: parsed rows into the database. Own throwaway database (JobTracker_Test_Importer).</summary>
public sealed class TrackerImporterTests : IDisposable
{
    private static readonly DateTimeOffset Now = new(2026, 11, 24, 12, 0, 0, TimeSpan.Zero);
    private readonly FakeClock _clock = new(Now);

    private const string Header =
        "application_id,company_name,job_title,date_applied,job_type,location,resume_variant,referral_type,application_status,interview_date,priority";

    public TrackerImporterTests()
    {
        using var db = NewContext();
        db.Database.EnsureDeleted();
        db.Database.Migrate();
    }

    public void Dispose()
    {
        using var db = NewContext();
        db.Database.EnsureDeleted();
    }

    private static JobTrackerDbContext NewContext() =>
        new(new DbContextOptionsBuilder<JobTrackerDbContext>()
            .UseSqlServer(@"Server=(localdb)\MSSQLLocalDB;Database=JobTracker_Test_Importer;Trusted_Connection=True;")
            .Options);

    private static TrackerParseResult Parse(params string[] rows) =>
        TrackerCsvParser.Parse(Header + "\n" + string.Join("\n", rows) + "\n");

    private ImportReport Run(TrackerParseResult parsed, bool write)
    {
        using var db = NewContext();
        return new TrackerImporter(db, _clock).Import(parsed, write);
    }

    private const string Applied = "1,Acme,Analyst,2026-10-05,Data Analyst,\"Austin, TX\",Data Analytics,Networking Contact,Applied,,Top Priority";
    private const string Rejected = "2,Beta,Engineer,2026-10-06,Software Engineer,,Backend SWE,Cold Apply,Rejected,,Unspecified";
    private const string Interviewed = "3,Gamma,Scientist,2026-10-07,Data Scientist,Remote,ML/Data Science,Cold Apply,Interviewed,2026-10-20,Low Priority";

    [Fact]
    public void A_dry_run_reports_what_would_happen_and_writes_nothing()
    {
        var report = Run(Parse(Applied, Rejected, Interviewed), write: false);

        Assert.False(report.Written);
        Assert.Equal(3, report.Imported.Count);
        using var db = NewContext();
        Assert.Equal(0, db.Applications.Count());
        Assert.Equal(0, db.StatusEvents.Count());
    }

    [Fact]
    public void A_real_run_saves_every_field_and_the_planned_events()
    {
        var report = Run(Parse(Applied, Interviewed), write: true);

        Assert.True(report.Written);
        using var db = NewContext();
        var acme = db.Applications.Single(a => a.CompanyName == "Acme");
        Assert.Equal("Analyst", acme.JobTitle);
        Assert.Equal(new DateOnly(2026, 10, 5), acme.DateApplied);
        Assert.Equal(JobType.DataAnalyst, acme.JobType);
        Assert.Equal("Austin, TX", acme.Location);
        Assert.Equal(ResumeVariant.DataAnalytics, acme.ResumeVariant);
        Assert.Equal(ReferralType.NetworkingContact, acme.ReferralType);
        Assert.Equal(Priority.TopPriority, acme.Priority);

        var gamma = db.Applications.Single(a => a.CompanyName == "Gamma");
        var events = db.StatusEvents.Where(e => e.ApplicationId == gamma.Id).OrderBy(e => e.Id).ToList();
        Assert.Equal(
            new[] { ApplicationStatus.Applied, ApplicationStatus.InterviewScheduled, ApplicationStatus.Interviewed },
            events.Select(e => e.NewStatus));
        Assert.Equal(new[] { true, false, true }, events.Select(e => e.DateKnown));
        Assert.All(events, e => Assert.Equal(EventSource.Import, e.Source));
        Assert.All(events, e => Assert.Equal(Now, e.RecordedAt));
    }

    [Fact]
    public void Imported_applications_project_to_their_tracker_status_with_no_problems()
    {
        Run(Parse(Applied, Rejected, Interviewed), write: true);

        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var statuses = service.List().ToDictionary(v => v.Application.CompanyName, v => v);
        Assert.Equal(ApplicationStatus.Rejected, statuses["Beta"].Projection.Status);
        Assert.Equal(ApplicationStatus.Interviewed, statuses["Gamma"].Projection.Status);
        Assert.All(statuses.Values, v => Assert.Empty(v.Projection.Problems));
    }

    [Fact]
    public void The_interview_date_comes_back_from_the_interviewed_event()
    {
        Run(Parse(Interviewed), write: true);

        using var db = NewContext();
        var view = Assert.Single(new ApplicationService(db, _clock).List());
        Assert.Equal(new DateTimeOffset(2026, 10, 20, 0, 0, 0, TimeSpan.Zero), view.Projection.FirstInterviewedAt);
    }

    [Fact]
    public void A_row_matching_an_existing_application_is_skipped_and_reported_ignoring_case()
    {
        using (var db = NewContext())
            new ApplicationService(db, _clock).AddApplication("ACME", "analyst", new DateOnly(2026, 10, 5));

        var report = Run(Parse(Applied, Rejected), write: true);

        Assert.Single(report.Imported);
        var skipped = Assert.Single(report.Skipped);
        Assert.Equal("1", skipped.SourceId);
        Assert.Contains("already", skipped.Reason, StringComparison.OrdinalIgnoreCase);
        using var db2 = NewContext();
        Assert.Equal(2, db2.Applications.Count());
    }

    [Fact]
    public void Running_the_same_import_twice_adds_nothing_the_second_time()
    {
        var parsed = Parse(Applied, Rejected, Interviewed);
        Run(parsed, write: true);
        int apps, events;
        using (var db = NewContext()) { apps = db.Applications.Count(); events = db.StatusEvents.Count(); }

        var second = Run(parsed, write: true);

        Assert.Empty(second.Imported);
        Assert.Equal(3, second.Skipped.Count);
        using var db2 = NewContext();
        Assert.Equal(apps, db2.Applications.Count());
        Assert.Equal(events, db2.StatusEvents.Count());
    }

    [Fact]
    public void A_duplicate_inside_the_file_is_imported_once_and_the_copy_reported()
    {
        var report = Run(Parse(Applied, "9,acme,ANALYST,2026-10-05,Other,,Unspecified,Cold Apply,Applied,,Unspecified"), write: true);

        Assert.Single(report.Imported);
        Assert.Equal("9", Assert.Single(report.Skipped).SourceId);
    }

    [Fact]
    public void Rows_the_parser_skipped_and_planner_warnings_appear_in_the_report()
    {
        var parsed = Parse(
            Applied,
            "5,NoDate,Analyst,,Other,,Unspecified,Cold Apply,Applied,,Unspecified",
            "6,Delta,Analyst,2026-10-05,Other,,Unspecified,Cold Apply,Applied,2026-10-10,Unspecified");

        var report = Run(parsed, write: true);

        Assert.Equal(2, report.Imported.Count);
        var skipped = Assert.Single(report.Skipped);
        Assert.Equal("5", skipped.SourceId);
        Assert.Contains("date applied", skipped.Reason, StringComparison.OrdinalIgnoreCase);
        var warning = Assert.Single(report.Warnings);
        Assert.Equal("6", warning.SourceId);
        Assert.Contains("interview date", warning.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void The_report_counts_flagged_events_per_row()
    {
        var report = Run(Parse(Interviewed), write: false);

        var row = Assert.Single(report.Imported);
        Assert.Equal(3, row.EventCount);
        Assert.Equal(1, row.FlaggedEvents);
    }

    [Fact]
    public void A_failure_part_way_through_saves_nothing()
    {
        var tooLong = new string('x', 201);
        var parsed = Parse(Applied, $"4,{tooLong},Analyst,2026-10-08,Other,,Unspecified,Cold Apply,Applied,,Unspecified");

        Assert.ThrowsAny<Exception>(() => Run(parsed, write: true));

        using var db = NewContext();
        Assert.Equal(0, db.Applications.Count());
        Assert.Equal(0, db.StatusEvents.Count());
    }
}
