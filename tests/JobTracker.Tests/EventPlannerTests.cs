using JobTracker.Data.Importing;
using JobTracker.Domain;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Importer piece 2b: which status events one tracker row becomes. Pure, no database.</summary>
public class EventPlannerTests
{
    private static readonly DateOnly Applied = new(2026, 10, 5);
    private static readonly DateOnly Interview = new(2026, 10, 20);

    private static ImportRow Row(ApplicationStatus status, DateOnly? interview = null) =>
        new(2, "1", "Acme", "Analyst", Applied, null, null, null, null, status, interview, null);

    private static string Shape(EventPlan plan) =>
        string.Join(" > ", plan.Events.Select(e => $"{e.NewStatus}{(e.DateKnown ? "" : "?")}@{e.Date:MM-dd}"));

    [Fact]
    public void Applied_is_one_known_event_on_the_date_applied()
    {
        var plan = EventPlanner.Plan(Row(ApplicationStatus.Applied));

        Assert.Equal("Applied@10-05", Shape(plan));
        Assert.Empty(plan.Warnings);
        Assert.Null(plan.Events[0].PreviousStatus);
    }

    [Fact]
    public void Not_started_is_a_single_first_event()
    {
        Assert.Equal("NotStarted@10-05", Shape(EventPlanner.Plan(Row(ApplicationStatus.NotStarted))));
    }

    [Theory]
    [InlineData(ApplicationStatus.Rejected, "Applied@10-05 > Rejected?@10-05")]
    [InlineData(ApplicationStatus.Withdrawn, "Applied@10-05 > Withdrawn?@10-05")]
    [InlineData(ApplicationStatus.InterviewScheduled, "Applied@10-05 > InterviewScheduled?@10-05")]
    public void A_response_with_no_known_date_gets_a_flagged_placeholder_at_the_date_applied(ApplicationStatus status, string expected)
    {
        var plan = EventPlanner.Plan(Row(status));

        Assert.Equal(expected, Shape(plan));
        Assert.Empty(plan.Warnings);
    }

    [Fact]
    public void Interviewed_with_an_interview_date_has_one_known_interview_event()
    {
        var plan = EventPlanner.Plan(Row(ApplicationStatus.Interviewed, Interview));

        Assert.Equal("Applied@10-05 > InterviewScheduled?@10-05 > Interviewed@10-20", Shape(plan));
    }

    [Fact]
    public void Interviewed_without_an_interview_date_flags_the_interview_event_too()
    {
        var plan = EventPlanner.Plan(Row(ApplicationStatus.Interviewed));

        Assert.Equal("Applied@10-05 > InterviewScheduled?@10-05 > Interviewed?@10-05", Shape(plan));
    }

    [Theory]
    [InlineData(ApplicationStatus.Rejected, "Rejected?@10-20")]
    [InlineData(ApplicationStatus.Withdrawn, "Withdrawn?@10-20")]
    [InlineData(ApplicationStatus.Accepted, "Accepted?@10-20")]
    public void A_final_status_after_a_known_interview_goes_through_it_and_takes_its_date(ApplicationStatus status, string last)
    {
        var plan = EventPlanner.Plan(Row(status, Interview));

        Assert.Equal($"Applied@10-05 > InterviewScheduled?@10-05 > Interviewed@10-20 > {last}", Shape(plan));
    }

    [Fact]
    public void Accepted_without_an_interview_date_still_goes_through_an_interview_all_flagged()
    {
        var plan = EventPlanner.Plan(Row(ApplicationStatus.Accepted));

        Assert.Equal(
            "Applied@10-05 > InterviewScheduled?@10-05 > Interviewed?@10-05 > Accepted?@10-05", Shape(plan));
    }

    [Theory]
    [InlineData(ApplicationStatus.Applied)]
    [InlineData(ApplicationStatus.NotStarted)]
    [InlineData(ApplicationStatus.InterviewScheduled)]
    public void An_interview_date_on_a_row_that_has_not_been_interviewed_is_not_used_and_is_reported(ApplicationStatus status)
    {
        var withDate = EventPlanner.Plan(Row(status, Interview));
        var without = EventPlanner.Plan(Row(status));

        Assert.Equal(Shape(without), Shape(withDate));
        Assert.Contains("interview date", Assert.Single(withDate.Warnings), StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void An_interview_date_before_the_date_applied_is_not_used_and_is_reported()
    {
        var plan = EventPlanner.Plan(Row(ApplicationStatus.Interviewed, new DateOnly(2026, 10, 1)));

        Assert.Equal("Applied@10-05 > InterviewScheduled?@10-05 > Interviewed?@10-05", Shape(plan));
        Assert.Contains("before", Assert.Single(plan.Warnings), StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void Every_status_with_and_without_an_interview_date_is_a_legal_ordered_chain_ending_in_the_row_status()
    {
        foreach (var status in Enum.GetValues<ApplicationStatus>())
        foreach (var interview in new DateOnly?[] { null, Interview, new DateOnly(2026, 10, 1) })
        {
            var plan = EventPlanner.Plan(Row(status, interview));
            var events = plan.Events;
            var label = $"{status} / {interview}";

            Assert.True(events.Count > 0, label);
            Assert.True(StatusTransitions.IsAllowedAsFirst(events[0].NewStatus), label);
            Assert.Null(events[0].PreviousStatus);
            Assert.True(events[0].DateKnown, label);
            Assert.Equal(Applied, events[0].Date);
            Assert.Equal(status, events[^1].NewStatus);

            for (var i = 1; i < events.Count; i++)
            {
                Assert.Equal(events[i - 1].NewStatus, events[i].PreviousStatus);
                Assert.True(StatusTransitions.IsAllowed(events[i - 1].NewStatus, events[i].NewStatus), label);
                Assert.True(events[i].Date >= events[i - 1].Date, label);
            }
        }
    }

    [Fact]
    public void Only_the_interview_event_ever_gets_a_known_date_besides_the_application_itself()
    {
        foreach (var status in Enum.GetValues<ApplicationStatus>())
        {
            var plan = EventPlanner.Plan(Row(status, Interview));

            foreach (var e in plan.Events.Skip(1).Where(e => e.DateKnown))
                Assert.Equal(ApplicationStatus.Interviewed, e.NewStatus);
        }
    }
}
