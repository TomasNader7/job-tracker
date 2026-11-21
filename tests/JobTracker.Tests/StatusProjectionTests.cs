using JobTracker.Domain;
using Xunit;

namespace JobTracker.Tests;

public class StatusProjectionTests
{
    private const int AppId = 42;
    private static readonly DateTimeOffset Day1 = new(2026, 10, 1, 9, 0, 0, TimeSpan.Zero);
    private static readonly DateTimeOffset AsOf = new(2026, 12, 1, 9, 0, 0, TimeSpan.Zero);

    private static StatusEvent Ev(long id, ApplicationStatus? prev, ApplicationStatus next, int dayOffset,
        int appId = AppId) =>
        StatusEvent.Create(id, appId, prev, next, Day1.AddDays(dayOffset), Day1.AddDays(dayOffset),
            EventSource.Manual);

    private static StatusEvent Fix(long id, long supersedes, ApplicationStatus? prev, ApplicationStatus next,
        int dayOffset, int appId = AppId) =>
        StatusEvent.Create(id, appId, prev, next, Day1.AddDays(dayOffset), Day1.AddDays(dayOffset + 1),
            EventSource.Correction, supersedes, "fix");

    [Fact]
    public void No_events_gives_null_status_and_no_problems()
    {
        var r = StatusProjection.Project([], AsOf);

        Assert.Null(r.Status);
        Assert.False(r.IsNoReply);
        Assert.Empty(r.Problems);
    }

    [Fact]
    public void Legal_chain_gives_the_last_status()
    {
        var r = StatusProjection.Project(
        [
            Ev(1, null, ApplicationStatus.Applied, 0),
            Ev(2, ApplicationStatus.Applied, ApplicationStatus.InterviewScheduled, 5),
            Ev(3, ApplicationStatus.InterviewScheduled, ApplicationStatus.Interviewed, 8),
        ], AsOf);

        Assert.Equal(ApplicationStatus.Interviewed, r.Status);
        Assert.Empty(r.Problems);
    }

    [Fact]
    public void Events_are_ordered_by_effective_date_not_input_order()
    {
        var r = StatusProjection.Project(
        [
            Ev(2, ApplicationStatus.Applied, ApplicationStatus.Rejected, 5),
            Ev(1, null, ApplicationStatus.Applied, 0),
        ], AsOf);

        Assert.Equal(ApplicationStatus.Rejected, r.Status);
        Assert.Empty(r.Problems);
    }

    [Fact]
    public void Illegal_step_stops_the_chain_at_the_last_valid_status_and_reports()
    {
        var r = StatusProjection.Project(
        [
            Ev(1, null, ApplicationStatus.Applied, 0),
            Ev(2, ApplicationStatus.Applied, ApplicationStatus.Accepted, 5),
            Ev(3, ApplicationStatus.Accepted, ApplicationStatus.Withdrawn, 6),
        ], AsOf);

        Assert.Equal(ApplicationStatus.Applied, r.Status);
        Assert.Single(r.Problems);
    }

    [Fact]
    public void Illegal_first_event_gives_null_status_and_reports()
    {
        var r = StatusProjection.Project([Ev(1, null, ApplicationStatus.Interviewed, 0)], AsOf);

        Assert.Null(r.Status);
        Assert.Single(r.Problems);
    }

    [Fact]
    public void Stored_previous_status_mismatch_is_reported_but_the_chain_wins()
    {
        var r = StatusProjection.Project(
        [
            Ev(1, null, ApplicationStatus.Applied, 0),
            Ev(2, ApplicationStatus.InterviewScheduled, ApplicationStatus.Rejected, 5),
        ], AsOf);

        Assert.Equal(ApplicationStatus.Rejected, r.Status);
        Assert.Single(r.Problems);
    }

    [Fact]
    public void Valid_correction_replaces_the_event_it_supersedes()
    {
        var r = StatusProjection.Project(
        [
            Ev(1, null, ApplicationStatus.Applied, 0),
            Ev(2, ApplicationStatus.Applied, ApplicationStatus.Rejected, 5),
            Fix(3, 2, ApplicationStatus.Applied, ApplicationStatus.InterviewScheduled, 5),
        ], AsOf);

        Assert.Equal(ApplicationStatus.InterviewScheduled, r.Status);
        Assert.Empty(r.Problems);
    }

    [Fact]
    public void Correction_pointing_at_a_missing_event_is_ignored_and_reported()
    {
        var r = StatusProjection.Project(
        [
            Ev(1, null, ApplicationStatus.Applied, 0),
            Fix(3, 99, ApplicationStatus.Applied, ApplicationStatus.Rejected, 5),
        ], AsOf);

        Assert.Equal(ApplicationStatus.Applied, r.Status);
        Assert.Single(r.Problems);
    }

    [Fact]
    public void Correction_pointing_at_another_applications_event_is_ignored_and_reported()
    {
        var r = StatusProjection.Project(
        [
            Ev(1, null, ApplicationStatus.Applied, 0),
            Fix(3, 77, ApplicationStatus.Applied, ApplicationStatus.Rejected, 5),
            Ev(77, null, ApplicationStatus.Applied, 0, appId: 7),
        ], AsOf);

        Assert.Equal(ApplicationStatus.Applied, r.Status);
        Assert.Single(r.Problems);
    }

    [Fact]
    public void Reopen_after_rejection_is_a_normal_transition()
    {
        var r = StatusProjection.Project(
        [
            Ev(1, null, ApplicationStatus.Applied, 0),
            Ev(2, ApplicationStatus.Applied, ApplicationStatus.Rejected, 5),
            Ev(3, ApplicationStatus.Rejected, ApplicationStatus.InterviewScheduled, 10),
        ], AsOf);

        Assert.Equal(ApplicationStatus.InterviewScheduled, r.Status);
        Assert.Empty(r.Problems);
    }

    [Fact]
    public void Applied_for_more_than_30_days_is_no_reply()
    {
        var r = StatusProjection.Project([Ev(1, null, ApplicationStatus.Applied, 0)], Day1.AddDays(31));

        Assert.Equal(ApplicationStatus.Applied, r.Status);
        Assert.True(r.IsNoReply);
    }

    [Fact]
    public void Applied_for_exactly_30_days_is_not_yet_no_reply()
    {
        var r = StatusProjection.Project([Ev(1, null, ApplicationStatus.Applied, 0)], Day1.AddDays(30));

        Assert.False(r.IsNoReply);
    }

    [Fact]
    public void Late_reply_clears_no_reply_with_no_special_event()
    {
        var r = StatusProjection.Project(
        [
            Ev(1, null, ApplicationStatus.Applied, 0),
            Ev(2, ApplicationStatus.Applied, ApplicationStatus.Rejected, 45),
        ], Day1.AddDays(60));

        Assert.Equal(ApplicationStatus.Rejected, r.Status);
        Assert.False(r.IsNoReply);
    }

    [Fact]
    public void Non_applied_status_is_never_no_reply()
    {
        var r = StatusProjection.Project([Ev(1, null, ApplicationStatus.NotStarted, 0)], Day1.AddDays(90));

        Assert.False(r.IsNoReply);
    }
}
