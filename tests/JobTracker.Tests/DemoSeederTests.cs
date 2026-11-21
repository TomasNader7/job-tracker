using JobTracker.Data;
using JobTracker.Data.Seeding;
using JobTracker.Domain;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

public class DemoDatabaseGuardTests
{
    private const string Local = @"(localdb)\MSSQLLocalDB";

    private static string Conn(string server, string? database) =>
        $"Server={server};{(database is null ? "" : $"Database={database};")}Trusted_Connection=True;";

    [Fact]
    public void Accepts_only_the_demo_database_on_localdb()
    {
        DemoDatabaseGuard.Require(Conn(Local, "JobTracker_Demo"));
    }

    [Theory]
    [InlineData("JobTracker")]          // the REAL database
    [InlineData("JobTracker_Demo2")]
    [InlineData("JobTracker_Demo_x")]
    [InlineData("jobtracker_demo")]     // exact name only
    [InlineData("master")]
    [InlineData("JobTracker_Test_Seed")]
    public void Refuses_every_other_database(string database)
    {
        var ex = Assert.Throws<DemoGuardException>(() => DemoDatabaseGuard.Require(Conn(Local, database)));
        Assert.Contains("refused", ex.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void Refuses_a_connection_with_no_database_name()
    {
        Assert.Throws<DemoGuardException>(() => DemoDatabaseGuard.Require(Conn(Local, null)));
    }

    [Theory]
    [InlineData("tcp:example.database.windows.net")]
    [InlineData("localhost")]
    [InlineData(".")]
    public void Refuses_a_database_that_is_not_on_localdb(string server)
    {
        Assert.Throws<DemoGuardException>(() => DemoDatabaseGuard.Require(Conn(server, "JobTracker_Demo")));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("not a connection string")]
    public void Refuses_a_missing_or_unreadable_connection_string(string? connection)
    {
        Assert.Throws<DemoGuardException>(() => DemoDatabaseGuard.Require(connection));
    }

    [Fact]
    public void Another_database_name_can_be_allowed_only_by_passing_it_explicitly()
    {
        DemoDatabaseGuard.Require(Conn(Local, "JobTracker_Test_Seed"), "JobTracker_Test_Seed");
        Assert.Throws<DemoGuardException>(() =>
            DemoDatabaseGuard.Require(Conn(Local, "JobTracker"), "JobTracker_Test_Seed"));
    }
}

/// <summary>Uses throwaway databases JobTracker_Test_Seed and JobTracker_Test_Seed2.</summary>
public sealed class DemoSeederTests : IDisposable
{
    private static readonly DateOnly AsOf = new(2026, 11, 16);
    private static readonly DateTimeOffset AsOfInstant = new(2026, 11, 16, 0, 0, 0, TimeSpan.Zero);
    private const string DbA = "JobTracker_Test_Seed";
    private const string DbB = "JobTracker_Test_Seed2";

    public DemoSeederTests()
    {
        foreach (var name in new[] { DbA, DbB })
        {
            using var db = NewContext(name);
            db.Database.EnsureDeleted();
            db.Database.Migrate();
        }
    }

    public void Dispose()
    {
        foreach (var name in new[] { DbA, DbB })
        {
            using var db = NewContext(name);
            db.Database.EnsureDeleted();
        }
    }

    private static JobTrackerDbContext NewContext(string database) =>
        new(new DbContextOptionsBuilder<JobTrackerDbContext>()
            .UseSqlServer($@"Server=(localdb)\MSSQLLocalDB;Database={database};Trusted_Connection=True;")
            .Options);

    private static IReadOnlyList<SeedRow> Rows() => DemoSeeder.ReadRows(RepoPaths.SyntheticDataDir);

    private static SeedResult SeedInto(string database)
    {
        using var db = NewContext(database);
        return new DemoSeeder(database).Seed(db, Rows(), AsOf);
    }

    [Fact]
    public void Reads_182_applications_and_skips_the_not_started_row()
    {
        var rows = Rows();

        Assert.Equal(182, rows.Count);
        Assert.DoesNotContain(rows, r => r.Status == "Not Started");
        Assert.All(rows, r => Assert.Matches(@"^Company \d{3}$", r.Company));
    }

    [Fact]
    public void Seeds_182_applications_with_zero_projection_problems()
    {
        var result = SeedInto(DbA);

        Assert.Equal(182, result.Applications);
        Assert.Equal(0, result.ProblemCount);
        using var db = NewContext(DbA);
        Assert.Equal(182, db.Applications.Count());
    }

    [Fact]
    public void Seeded_totals_tie_back_to_the_phase_0_expected_measures()
    {
        SeedInto(DbA);

        using var db = NewContext(DbA);
        var views = new ApplicationService(db, new FakeClock(AsOfInstant)).List();
        int Count(Func<ApplicationView, bool> f) => views.Count(f);

        Assert.Equal(182, Count(v => v.Projection.Status != ApplicationStatus.NotStarted && v.Projection.Status is not null));
        Assert.Equal(54, Count(v => v.Projection.Status == ApplicationStatus.Rejected));
        Assert.Equal(2, Count(v => v.Projection.Status == ApplicationStatus.Interviewed));
        Assert.Equal(1, Count(v => v.Projection.Status == ApplicationStatus.Withdrawn));
        Assert.Equal(72, Count(v => v.Projection.IsNoReply));
        Assert.Equal(53, Count(v => v.Projection.Status == ApplicationStatus.Applied && !v.Projection.IsNoReply));
        Assert.Equal(56, Count(v => v.Projection.Status is ApplicationStatus.Rejected or ApplicationStatus.Interviewed));
    }

    [Fact]
    public void Interviewed_applications_get_the_csv_interview_date()
    {
        SeedInto(DbA);
        var expected = Rows().Where(r => r.InterviewDate is not null)
            .ToDictionary(r => r.Company, r => r.InterviewDate!.Value);

        using var db = NewContext(DbA);
        var views = new ApplicationService(db, new FakeClock(AsOfInstant)).List();

        Assert.Equal(2, expected.Count);
        foreach (var v in views.Where(v => expected.ContainsKey(v.Application.CompanyName)))
        {
            var date = DateOnly.FromDateTime(v.Projection.FirstInterviewedAt!.Value.UtcDateTime);
            Assert.Equal(expected[v.Application.CompanyName], date);
        }
    }

    [Fact]
    public void Includes_a_fixed_small_set_of_corrections_including_a_correction_of_a_correction()
    {
        var result = SeedInto(DbA);

        using var db = NewContext(DbA);
        var corrections = db.StatusEvents.Where(e => e.Source == EventSource.Correction).ToList();

        Assert.Equal(7, result.Corrections);
        Assert.Equal(7, corrections.Count);
        Assert.All(corrections, c => Assert.Equal("synthetic correction", c.Note));
        var ids = corrections.Select(c => c.Id).ToHashSet();
        Assert.Contains(corrections, c => ids.Contains(c.SupersedesEventId!.Value));
    }

    [Fact]
    public void No_event_is_before_the_application_date_or_after_as_of()
    {
        SeedInto(DbA);

        using var db = NewContext(DbA);
        var applied = db.Applications.ToDictionary(a => a.Id, a => a.DateApplied);
        foreach (var e in db.StatusEvents.ToList())
        {
            var day = DateOnly.FromDateTime(e.EffectiveAt.UtcDateTime);
            Assert.True(day >= applied[e.ApplicationId], $"event {e.Id} before application date");
            Assert.True(day <= AsOf, $"event {e.Id} after as-of");
        }
    }

    [Fact]
    public void Notes_are_only_from_the_allowlist()
    {
        SeedInto(DbA);

        using var db = NewContext(DbA);
        var notes = db.StatusEvents.Select(e => e.Note).Distinct().ToList();

        Assert.All(notes, n => Assert.Contains(n, new string?[] { null, "synthetic seed", "synthetic correction" }));
    }

    [Fact]
    public void Seeding_twice_into_fresh_databases_gives_identical_data()
    {
        SeedInto(DbA);
        SeedInto(DbB);

        static List<string> Dump(string database)
        {
            using var db = NewContext(database);
            var apps = db.Applications.OrderBy(a => a.Id)
                .Select(a => $"A|{a.Id}|{a.CompanyName}|{a.JobTitle}|{a.DateApplied:yyyy-MM-dd}").ToList();
            var events = db.StatusEvents.OrderBy(e => e.Id).ToList()
                .Select(e => $"E|{e.Id}|{e.ApplicationId}|{e.PreviousStatus}|{e.NewStatus}|{e.EffectiveAt:O}|{e.RecordedAt:O}|{e.Source}|{e.SupersedesEventId}|{e.Note}");
            return apps.Concat(events).ToList();
        }

        var a = Dump(DbA);
        var b = Dump(DbB);

        Assert.True(a.Count > 182);
        Assert.Equal(a, b);
    }

    [Fact]
    public void Refuses_to_seed_a_database_that_already_has_applications()
    {
        SeedInto(DbA);

        using var db = NewContext(DbA);
        var ex = Assert.Throws<InvalidOperationException>(() => new DemoSeeder(DbA).Seed(db, Rows(), AsOf));
        Assert.Contains("not empty", ex.Message);
        Assert.Equal(182, db.Applications.Count());
    }

    [Fact]
    public void Refuses_a_database_other_than_the_one_it_was_told_to_allow()
    {
        using var db = NewContext(DbA);

        // Default seeder allows only JobTracker_Demo, so a test database is refused.
        Assert.Throws<DemoGuardException>(() => new DemoSeeder().Seed(db, Rows(), AsOf));
        Assert.Empty(db.Applications);
    }
}
