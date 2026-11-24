using JobTracker.Data;
using JobTracker.Domain;
using JobTracker.Web.Pages;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Location on the domain object, the service and the Add page. Own throwaway database (JobTracker_Test_Location).</summary>
public sealed class LocationTests : IDisposable
{
    private static readonly DateTimeOffset Now = new(2026, 11, 21, 12, 0, 0, TimeSpan.Zero);
    private readonly FakeClock _clock = new(Now);

    public LocationTests()
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
            .UseSqlServer(@"Server=(localdb)\MSSQLLocalDB;Database=JobTracker_Test_Location;Trusted_Connection=True;")
            .Options);

    [Fact]
    public void An_application_can_carry_a_location_and_is_valid_without_one()
    {
        var with = new Application { CompanyName = "Acme", JobTitle = "Analyst", DateApplied = new(2026, 11, 1), Location = "Austin, TX" };
        var without = new Application { CompanyName = "Acme", JobTitle = "Analyst", DateApplied = new(2026, 11, 1) };

        Assert.Equal("Austin, TX", with.Location);
        Assert.Null(without.Location);
    }

    [Fact]
    public void The_service_saves_the_location_trimmed()
    {
        using (var db = NewContext())
            new ApplicationService(db, _clock).AddApplication(
                "Acme", "Analyst", new DateOnly(2026, 11, 20), location: "  Austin, TX  ");

        using var db2 = NewContext();
        var saved = Assert.Single(new ApplicationService(db2, _clock).List());
        Assert.Equal("Austin, TX", saved.Application.Location);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void A_blank_location_is_saved_as_null(string? blank)
    {
        using (var db = NewContext())
            new ApplicationService(db, _clock).AddApplication(
                "Acme", "Analyst", new DateOnly(2026, 11, 20), location: blank);

        using var db2 = NewContext();
        Assert.Null(Assert.Single(new ApplicationService(db2, _clock).List()).Application.Location);
    }

    [Fact]
    public void Add_page_saves_the_typed_location()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var page = new AddModel(service, _clock)
        {
            CompanyName = "Acme",
            JobTitle = "Analyst",
            DateApplied = new DateOnly(2026, 11, 20),
            Location = "Remote",
        };

        var result = page.OnPost();

        Assert.IsType<RedirectToPageResult>(result);
        Assert.Equal("Remote", Assert.Single(service.List()).Application.Location);
    }
}
