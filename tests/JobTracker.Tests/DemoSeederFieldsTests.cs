using JobTracker.Data;
using JobTracker.Data.Seeding;
using JobTracker.Domain;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Contract v2 slice 1: the seeder keeps the five optional fields. Own database JobTracker_Test_SeedFields.</summary>
public sealed class DemoSeederFieldsTests : IDisposable
{
    private const string Db = "JobTracker_Test_SeedFields";
    private static readonly DateOnly AsOf = new(2026, 11, 16);

    public DemoSeederFieldsTests()
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
            .UseSqlServer($@"Server=(localdb)\MSSQLLocalDB;Database={Db};Trusted_Connection=True;").Options);

    private static IReadOnlyList<SeedRow> Rows() => DemoSeeder.ReadRows(RepoPaths.SyntheticDataDir);

    [Fact]
    public void Reading_the_csv_keeps_the_five_fields_of_a_known_row()
    {
        // fact_application.csv: Company 009 = job type 7, "Austin, TX", resume variant 5, Cold Apply, priority 2.
        var row = Rows().Single(r => r.Company == "Company 009");

        Assert.Equal(JobType.SoftwareEngineer, row.JobType);
        Assert.Equal("Austin, TX", row.Location);
        Assert.Equal(ResumeVariant.DataEngineering, row.ResumeVariant);
        Assert.Equal(ReferralType.ColdApply, row.ReferralType);
        Assert.Equal(Priority.MediumPriority, row.Priority);
    }

    [Fact]
    public void Unspecified_priority_becomes_null()
    {
        // priority key 4 = "Unspecified" appears 10 times in the synthetic file.
        Assert.Equal(10, Rows().Count(r => r.Priority is null));
    }

    [Fact]
    public void Seeding_saves_the_fields_on_the_application()
    {
        using var db = NewContext();
        new DemoSeeder(Db).Seed(db, Rows(), AsOf);

        var app = db.Applications.Single(a => a.CompanyName == "Company 009");
        Assert.Equal(JobType.SoftwareEngineer, app.JobType);
        Assert.Equal("Austin, TX", app.Location);
        Assert.Equal(ResumeVariant.DataEngineering, app.ResumeVariant);
        Assert.Equal(ReferralType.ColdApply, app.ReferralType);
        Assert.Equal(Priority.MediumPriority, app.Priority);
    }
}
