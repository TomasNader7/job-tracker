using JobTracker.Data.Snapshots;
using JobTracker.Domain;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Contract v2: gold_funnel_by_dimension. Pure computation, no database.</summary>
public sealed class DimensionFunnelTests
{
    private static AppOutcome Out(int id, ApplicationStatus? status, JobType? jobType = null, ResumeVariant? variant = null,
        Priority? priority = null, ReferralType? referral = null, bool noReply = false) =>
        new(id, status, noReply, 0, new DateOnly(2026, 10, 1), null, jobType, variant, priority, referral);

    private static readonly AppOutcome[] Sample =
    [
        Out(1, ApplicationStatus.Rejected, JobType.DataAnalyst, ResumeVariant.DataAnalytics, Priority.TopPriority, ReferralType.ColdApply),
        Out(2, ApplicationStatus.Applied, JobType.DataAnalyst, ResumeVariant.DataAnalytics, null, ReferralType.NetworkingContact, noReply: true),
        Out(3, ApplicationStatus.Interviewed, JobType.DataEngineer, null, Priority.TopPriority, ReferralType.NetworkingContact),
        Out(4, ApplicationStatus.NotStarted, JobType.DataEngineer, null, null, null),   // not an application: never counted
        Out(5, null, JobType.DataEngineer, null, null, null),                            // no valid history: never counted
    ];

    [Fact]
    public void Groups_are_listed_by_fixed_dimension_order_then_value_and_null_is_unspecified()
    {
        var rows = DimensionFunnel.Compute(Sample);

        Assert.Equal(
        [
            "job_type|DataAnalyst", "job_type|DataEngineer",
            "resume_variant|DataAnalytics", "resume_variant|Unspecified",
            "priority|TopPriority", "priority|Unspecified",
            "referral_type|ColdApply", "referral_type|NetworkingContact",
        ], rows.Select(r => $"{r.Dimension}|{r.Value}"));
    }

    [Fact]
    public void Each_group_uses_the_same_measure_rules_as_the_overall_funnel()
    {
        var rows = DimensionFunnel.Compute(Sample);

        var analyst = rows.Single(r => r is { Dimension: "job_type", Value: "DataAnalyst" }).Measures;
        Assert.Equal(2, analyst.Applications);
        Assert.Equal(1, analyst.Responses);        // the Rejected one
        Assert.Equal(1, analyst.Rejections);
        Assert.Equal(1, analyst.NoReplies);
        Assert.Equal(0.5000m, analyst.ResponsesRate);

        var engineer = rows.Single(r => r is { Dimension: "job_type", Value: "DataEngineer" }).Measures;
        Assert.Equal(1, engineer.Applications);    // NotStarted and no-history rows do not count
        Assert.Equal(1, engineer.Interviews);
    }

    [Fact]
    public void Every_dimension_adds_up_to_the_overall_application_count()
    {
        var rows = DimensionFunnel.Compute(Sample);
        var overall = FunnelMeasures.Compute(Sample).Applications;

        foreach (var dimension in DimensionFunnel.Dimensions)
            Assert.Equal(overall, rows.Where(r => r.Dimension == dimension).Sum(r => r.Measures.Applications));
    }

    [Fact]
    public void A_group_with_no_applications_is_left_out()
    {
        var rows = DimensionFunnel.Compute([Out(1, ApplicationStatus.NotStarted, JobType.Other)]);

        Assert.Empty(rows);
    }

    [Fact]
    public void Hash_text_is_tab_separated_with_counts_then_four_decimal_rates_and_ends_in_lf()
    {
        var text = DimensionFunnel.HashText(DimensionFunnel.Compute(
            [Out(1, ApplicationStatus.Rejected, JobType.DataAnalyst),
             Out(2, ApplicationStatus.Applied, JobType.DataAnalyst, noReply: true)]));

        // dimension, value, Applications, Responses, Interviews, Offers, Rejections, No_Replies, 5 rates
        var expectedFirst = "job_type\tDataAnalyst\t2\t1\t0\t0\t1\t1\t0.5000\t0.0000\t0.0000\t0.5000\t0.5000\n";
        Assert.StartsWith(expectedFirst, text);
        Assert.EndsWith("\n", text);
        Assert.Equal(4, text.Split('\n', StringSplitOptions.RemoveEmptyEntries).Length); // one row per dimension
        Assert.DoesNotContain("\r", text);
        Assert.Contains("resume_variant\tUnspecified\t2\t", text);
    }

    [Fact]
    public void Values_sort_by_ordinal_text_so_a_case_difference_cannot_reorder_the_hash()
    {
        var rows = DimensionFunnel.Compute(
        [
            Out(1, ApplicationStatus.Applied, JobType.SoftwareEngineer),
            Out(2, ApplicationStatus.Applied, JobType.BiAnalyst),
            Out(3, ApplicationStatus.Applied, JobType.Other),
        ]);

        Assert.Equal(["BiAnalyst", "Other", "SoftwareEngineer"],
            rows.Where(r => r.Dimension == "job_type").Select(r => r.Value));
    }
}
