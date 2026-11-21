using JobTracker.Domain;
using Xunit;
using static JobTracker.Domain.ApplicationStatus;

namespace JobTracker.Tests;

public class StatusTransitionTests
{
    public static TheoryData<ApplicationStatus, ApplicationStatus> LegalTransitions => new()
    {
        { NotStarted, Applied },
        { NotStarted, Withdrawn },
        { Applied, InterviewScheduled },
        { Applied, Rejected },
        { Applied, Withdrawn },
        { InterviewScheduled, Interviewed },
        { InterviewScheduled, Rejected },
        { InterviewScheduled, Withdrawn },
        { Interviewed, InterviewScheduled },
        { Interviewed, Rejected },
        { Interviewed, Accepted },
        { Interviewed, Withdrawn },
        { Rejected, InterviewScheduled }, // explicit reopen
    };

    [Theory]
    [MemberData(nameof(LegalTransitions))]
    public void Legal_transition_is_allowed(ApplicationStatus from, ApplicationStatus to)
        => Assert.True(StatusTransitions.IsAllowed(from, to));

    [Fact]
    public void Every_other_pair_is_rejected()
    {
        var legal = LegalTransitions.Select(row => ((ApplicationStatus)row[0], (ApplicationStatus)row[1])).ToHashSet();
        var all = Enum.GetValues<ApplicationStatus>();

        foreach (var from in all)
            foreach (var to in all)
                Assert.Equal(legal.Contains((from, to)), StatusTransitions.IsAllowed(from, to));
    }

    [Theory]
    [InlineData(Applied, Interviewed)]
    [InlineData(Applied, Accepted)]
    [InlineData(Rejected, Applied)]
    [InlineData(Rejected, Accepted)]
    public void Skipping_steps_or_undoing_a_rejection_is_rejected(ApplicationStatus from, ApplicationStatus to)
        => Assert.False(StatusTransitions.IsAllowed(from, to));

    [Theory]
    [InlineData(Withdrawn)]
    [InlineData(Accepted)]
    public void Terminal_statuses_allow_nothing(ApplicationStatus terminal)
        => Assert.Empty(StatusTransitions.AllowedNext(terminal));

    [Fact]
    public void A_status_never_transitions_to_itself()
    {
        foreach (var status in Enum.GetValues<ApplicationStatus>())
            Assert.False(StatusTransitions.IsAllowed(status, status));
    }

    [Fact]
    public void Only_Rejected_to_InterviewScheduled_is_a_reopen()
    {
        Assert.True(StatusTransitions.IsReopen(Rejected, InterviewScheduled));
        Assert.False(StatusTransitions.IsReopen(Applied, InterviewScheduled));
        Assert.False(StatusTransitions.IsReopen(Interviewed, InterviewScheduled));
    }

    [Theory]
    [InlineData(NotStarted)]
    [InlineData(Applied)]
    public void A_first_event_may_start_as_NotStarted_or_Applied(ApplicationStatus first)
        => Assert.True(StatusTransitions.IsAllowedAsFirst(first));

    [Theory]
    [InlineData(Rejected)]
    [InlineData(InterviewScheduled)]
    [InlineData(Interviewed)]
    [InlineData(Withdrawn)]
    [InlineData(Accepted)]
    public void A_first_event_cannot_start_anywhere_else(ApplicationStatus first)
        => Assert.False(StatusTransitions.IsAllowedAsFirst(first));
}
