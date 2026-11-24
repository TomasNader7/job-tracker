using JobTracker.Data;
using JobTracker.Domain;
using JobTracker.Web;
using JobTracker.Web.Pages;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Referral type through the service and the Add page. Own throwaway database (JobTracker_Test_ReferralType).</summary>
public sealed class ReferralTypeUiTests : IDisposable
{
    private static readonly DateTimeOffset Now = new(2026, 11, 21, 12, 0, 0, TimeSpan.Zero);
    private readonly FakeClock _clock = new(Now);

    public ReferralTypeUiTests()
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
            .UseSqlServer(@"Server=(localdb)\MSSQLLocalDB;Database=JobTracker_Test_ReferralType;Trusted_Connection=True;")
            .Options);

    [Fact]
    public void The_service_saves_the_referral_type_it_is_given()
    {
        using (var db = NewContext())
            new ApplicationService(db, _clock).AddApplication(
                "Acme", "Analyst", new DateOnly(2026, 11, 20), referralType: ReferralType.NetworkingContact);

        using var db2 = NewContext();
        var saved = Assert.Single(new ApplicationService(db2, _clock).List());
        Assert.Equal(ReferralType.NetworkingContact, saved.Application.ReferralType);
    }

    [Fact]
    public void The_service_keeps_all_four_optional_fields_independent()
    {
        using (var db = NewContext())
            new ApplicationService(db, _clock).AddApplication(
                "Acme", "Analyst", new DateOnly(2026, 11, 20), JobType.DataEngineer, ResumeVariant.FrontendSwe,
                Priority.LowPriority, ReferralType.ColdApply);

        using var db2 = NewContext();
        var saved = Assert.Single(new ApplicationService(db2, _clock).List()).Application;
        Assert.Equal(JobType.DataEngineer, saved.JobType);
        Assert.Equal(ResumeVariant.FrontendSwe, saved.ResumeVariant);
        Assert.Equal(Priority.LowPriority, saved.Priority);
        Assert.Equal(ReferralType.ColdApply, saved.ReferralType);
    }

    [Fact]
    public void Add_page_saves_the_chosen_referral_type()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var page = new AddModel(service, _clock)
        {
            CompanyName = "Acme",
            JobTitle = "Analyst",
            DateApplied = new DateOnly(2026, 11, 20),
            ReferralType = ReferralType.ColdApply,
        };

        var result = page.OnPost();

        Assert.IsType<RedirectToPageResult>(result);
        Assert.Equal(ReferralType.ColdApply, Assert.Single(service.List()).Application.ReferralType);
    }

    [Fact]
    public void Add_page_leaves_referral_type_empty_when_none_is_chosen()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var page = new AddModel(service, _clock)
        {
            CompanyName = "Acme",
            JobTitle = "Analyst",
            DateApplied = new DateOnly(2026, 11, 20),
        };

        page.OnPost();

        Assert.Null(Assert.Single(service.List()).Application.ReferralType);
    }

    [Fact]
    public void The_list_shows_a_dash_when_an_application_has_no_referral_type()
    {
        Assert.Equal("-", ReferralTypeDisplay.LabelOrDash(null));
    }

    [Fact]
    public void The_list_shows_the_label_when_an_application_has_a_referral_type()
    {
        Assert.Equal("Networking Contact", ReferralTypeDisplay.LabelOrDash(ReferralType.NetworkingContact));
    }

    [Fact]
    public void Dropdown_labels_are_the_two_dashboard_names_in_order()
    {
        // The dashboard has no dim file for this: fact_application.csv holds the words directly
        // (see scripts/starschema.py REFERRAL_TYPES).
        var labels = Enum.GetValues<ReferralType>().Select(ReferralTypeDisplay.Label).ToList();

        Assert.Equal(new[] { "Networking Contact", "Cold Apply" }, labels);
    }
}
