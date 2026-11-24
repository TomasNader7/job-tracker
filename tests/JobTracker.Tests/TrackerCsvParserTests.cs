using JobTracker.Data.Importing;
using JobTracker.Domain;
using JobTracker.Web;
using Xunit;

namespace JobTracker.Tests;

/// <summary>The importer's first piece: clean_rows.csv text in, typed rows and a skipped-rows report out. No database.</summary>
public class TrackerCsvParserTests
{
    private const string Header =
        "application_id,company_name,job_title,date_applied,job_type,location,resume_variant,referral_type,application_status,interview_date,priority";

    private static string Csv(params string[] rows) => Header + "\n" + string.Join("\n", rows) + "\n";

    private const string GoodRow =
        "7,Acme,Data Analyst,2026-10-05,Data Analyst,\"Austin, TX\",Data Analytics,Networking Contact,Interviewed,2026-10-20,Top Priority";

    [Fact]
    public void A_complete_row_becomes_typed_values()
    {
        var result = TrackerCsvParser.Parse(Csv(GoodRow));

        var row = Assert.Single(result.Rows);
        Assert.Empty(result.Skipped);
        Assert.Equal("7", row.SourceId);
        Assert.Equal(2, row.SourceRow);
        Assert.Equal("Acme", row.CompanyName);
        Assert.Equal("Data Analyst", row.JobTitle);
        Assert.Equal(new DateOnly(2026, 10, 5), row.DateApplied);
        Assert.Equal(JobType.DataAnalyst, row.JobType);
        Assert.Equal("Austin, TX", row.Location);
        Assert.Equal(ResumeVariant.DataAnalytics, row.ResumeVariant);
        Assert.Equal(ReferralType.NetworkingContact, row.ReferralType);
        Assert.Equal(ApplicationStatus.Interviewed, row.Status);
        Assert.Equal(new DateOnly(2026, 10, 20), row.InterviewDate);
        Assert.Equal(Priority.TopPriority, row.Priority);
    }

    [Fact]
    public void Unspecified_and_blank_optional_values_become_null()
    {
        var result = TrackerCsvParser.Parse(Csv("8,Acme,Analyst,2026-10-05,Other,,Unspecified,,Applied,,Unspecified"));

        var row = Assert.Single(result.Rows);
        Assert.Null(row.Location);
        Assert.Null(row.ResumeVariant);
        Assert.Null(row.ReferralType);
        Assert.Null(row.InterviewDate);
        Assert.Null(row.Priority);
    }

    [Fact]
    public void A_row_with_no_date_applied_is_skipped_and_reported_and_the_rest_still_parse()
    {
        var result = TrackerCsvParser.Parse(Csv(
            GoodRow,
            "9,Beta,Analyst,,Other,,Unspecified,Cold Apply,Applied,,Unspecified",
            "10,Gamma,Analyst,2026-10-06,Other,,Unspecified,Cold Apply,Applied,,Unspecified"));

        Assert.Equal(new[] { "7", "10" }, result.Rows.Select(r => r.SourceId));
        var skipped = Assert.Single(result.Skipped);
        Assert.Equal("9", skipped.SourceId);
        Assert.Equal(3, skipped.SourceRow);
        Assert.Contains("date applied", skipped.Reason, StringComparison.OrdinalIgnoreCase);
    }

    [Theory]
    [InlineData("not-a-date")]
    [InlineData("2026-13-45")]
    [InlineData("10/05/2026")]
    public void A_date_that_is_not_a_real_iso_date_is_skipped_not_guessed(string bad)
    {
        var result = TrackerCsvParser.Parse(Csv($"9,Beta,Analyst,{bad},Other,,Unspecified,Cold Apply,Applied,,Unspecified"));

        Assert.Empty(result.Rows);
        Assert.Contains("date applied", Assert.Single(result.Skipped).Reason, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void A_bad_interview_date_skips_the_row_and_names_the_column()
    {
        var result = TrackerCsvParser.Parse(Csv("9,Beta,Analyst,2026-10-05,Other,,Unspecified,Cold Apply,Interviewed,soon,Unspecified"));

        Assert.Empty(result.Rows);
        Assert.Contains("interview date", Assert.Single(result.Skipped).Reason, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void An_unknown_job_type_skips_the_row_and_names_the_column()
    {
        var result = TrackerCsvParser.Parse(Csv("9,Beta,Analyst,2026-10-05,Wizard,,Unspecified,Cold Apply,Applied,,Unspecified"));

        Assert.Empty(result.Rows);
        Assert.Contains("job type", Assert.Single(result.Skipped).Reason, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void Unknown_status_resume_variant_priority_and_referral_words_are_skipped()
    {
        var result = TrackerCsvParser.Parse(Csv(
            "1,A,T,2026-10-05,Other,,Unspecified,Cold Apply,Pending,,Unspecified",
            "2,B,T,2026-10-05,Other,,Rust,Cold Apply,Applied,,Unspecified",
            "3,C,T,2026-10-05,Other,,Unspecified,Cold Apply,Applied,,Urgent",
            "4,D,T,2026-10-05,Other,,Unspecified,Friend,Applied,,Unspecified"));

        Assert.Empty(result.Rows);
        Assert.Equal(4, result.Skipped.Count);
        Assert.Contains("status", result.Skipped[0].Reason, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("resume variant", result.Skipped[1].Reason, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("priority", result.Skipped[2].Reason, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("referral", result.Skipped[3].Reason, StringComparison.OrdinalIgnoreCase);
    }

    [Theory]
    [InlineData(",Analyst")]
    [InlineData("Acme,")]
    public void A_row_missing_company_or_title_is_skipped(string companyAndTitle)
    {
        var result = TrackerCsvParser.Parse(Csv($"9,{companyAndTitle},2026-10-05,Other,,Unspecified,Cold Apply,Applied,,Unspecified"));

        Assert.Empty(result.Rows);
        Assert.Single(result.Skipped);
    }

    [Theory]
    [InlineData("Not Started", ApplicationStatus.NotStarted)]
    [InlineData("Applied", ApplicationStatus.Applied)]
    [InlineData("No Reply", ApplicationStatus.Applied)] // derived in the app, never stored
    [InlineData("Withdrawn", ApplicationStatus.Withdrawn)]
    [InlineData("Rejected", ApplicationStatus.Rejected)]
    [InlineData("Interview Scheduled", ApplicationStatus.InterviewScheduled)]
    [InlineData("Interviewed", ApplicationStatus.Interviewed)]
    [InlineData("Accepted", ApplicationStatus.Accepted)]
    public void Every_tracker_status_word_maps_to_an_app_status(string word, ApplicationStatus expected)
    {
        var result = TrackerCsvParser.Parse(Csv($"9,Beta,Analyst,2026-10-05,Other,,Unspecified,Cold Apply,{word},,Unspecified"));

        Assert.Equal(expected, Assert.Single(result.Rows).Status);
    }

    [Fact]
    public void Every_label_the_app_shows_is_understood_by_the_parser()
    {
        foreach (var type in Enum.GetValues<JobType>())
            Assert.Equal(type, ParseOne(jobType: JobTypeDisplay.Label(type)).JobType);
        foreach (var variant in Enum.GetValues<ResumeVariant>())
            Assert.Equal(variant, ParseOne(resumeVariant: ResumeVariantDisplay.Label(variant)).ResumeVariant);
        foreach (var priority in Enum.GetValues<Priority>())
            Assert.Equal(priority, ParseOne(priority: PriorityDisplay.Label(priority)).Priority);
        foreach (var referral in Enum.GetValues<ReferralType>())
            Assert.Equal(referral, ParseOne(referral: ReferralTypeDisplay.Label(referral)).ReferralType);
    }

    [Fact]
    public void A_missing_required_column_in_the_header_throws()
    {
        var text = "application_id,company_name,job_title\n1,Acme,Analyst\n";

        var ex = Assert.Throws<ImportFormatException>(() => TrackerCsvParser.Parse(text));

        Assert.Contains("date_applied", ex.Message);
    }

    [Fact]
    public void Columns_may_come_in_any_order()
    {
        var text = "job_title,company_name,application_id,date_applied,job_type,location,resume_variant,referral_type,application_status,interview_date,priority\n"
                   + "Analyst,Acme,5,2026-10-05,Other,,Unspecified,Cold Apply,Applied,,Unspecified\n";

        var row = Assert.Single(TrackerCsvParser.Parse(text).Rows);

        Assert.Equal("Acme", row.CompanyName);
        Assert.Equal("Analyst", row.JobTitle);
        Assert.Equal("5", row.SourceId);
    }

    [Fact]
    public void A_header_only_file_gives_an_empty_result_and_an_empty_file_throws()
    {
        var result = TrackerCsvParser.Parse(Header + "\n");
        Assert.Empty(result.Rows);
        Assert.Empty(result.Skipped);

        Assert.Throws<ImportFormatException>(() => TrackerCsvParser.Parse(""));
    }

    [Fact]
    public void A_utf8_byte_order_mark_at_the_start_is_ignored()
    {
        var result = TrackerCsvParser.Parse("﻿" + Csv(GoodRow));

        Assert.Single(result.Rows);
    }

    private static ImportRow ParseOne(string jobType = "Other", string resumeVariant = "Unspecified",
        string priority = "Unspecified", string referral = "Cold Apply") =>
        Assert.Single(TrackerCsvParser.Parse(
            Csv($"1,Acme,Analyst,2026-10-05,{jobType},,{resumeVariant},{referral},Applied,,{priority}")).Rows);
}
