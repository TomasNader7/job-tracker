using JobTracker.Domain;
using Xunit;

namespace JobTracker.Tests;

public class StatusEventTests
{
    private static readonly DateTimeOffset Effective = new(2026, 11, 1, 9, 0, 0, TimeSpan.Zero);
    private static readonly DateTimeOffset Recorded = new(2026, 11, 2, 9, 0, 0, TimeSpan.Zero);

    [Fact]
    public void Manual_event_without_supersedes_needs_no_note()
    {
        var e = StatusEvent.Create(1, 42, ApplicationStatus.Applied, ApplicationStatus.InterviewScheduled,
            Effective, Recorded, EventSource.Manual);

        Assert.Null(e.SupersedesEventId);
        Assert.Null(e.Note);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Correction_without_a_note_is_rejected(string? note)
    {
        Assert.Throws<ArgumentException>(() => StatusEvent.Create(2, 42, ApplicationStatus.Applied,
            ApplicationStatus.Rejected, Effective, Recorded, EventSource.Correction,
            supersedesEventId: 1, note: note));
    }

    [Fact]
    public void Correction_with_a_note_is_accepted()
    {
        var e = StatusEvent.Create(2, 42, ApplicationStatus.Applied, ApplicationStatus.Rejected,
            Effective, Recorded, EventSource.Correction, supersedesEventId: 1, note: "Typed the wrong status");

        Assert.Equal(1, e.SupersedesEventId);
        Assert.Equal("Typed the wrong status", e.Note);
    }

    [Fact]
    public void Supersedes_requires_the_Correction_source_and_vice_versa()
    {
        Assert.Throws<ArgumentException>(() => StatusEvent.Create(2, 42, ApplicationStatus.Applied,
            ApplicationStatus.Rejected, Effective, Recorded, EventSource.Manual,
            supersedesEventId: 1, note: "x"));

        Assert.Throws<ArgumentException>(() => StatusEvent.Create(2, 42, ApplicationStatus.Applied,
            ApplicationStatus.Rejected, Effective, Recorded, EventSource.Correction,
            supersedesEventId: null, note: "x"));
    }

    [Fact]
    public void An_event_cannot_supersede_itself()
    {
        Assert.Throws<ArgumentException>(() => StatusEvent.Create(5, 42, ApplicationStatus.Applied,
            ApplicationStatus.Rejected, Effective, Recorded, EventSource.Correction,
            supersedesEventId: 5, note: "x"));
    }

    [Fact]
    public void Events_are_immutable_records_with_value_equality()
    {
        var a = StatusEvent.Create(1, 42, null, ApplicationStatus.Applied, Effective, Recorded, EventSource.Import);
        var b = StatusEvent.Create(1, 42, null, ApplicationStatus.Applied, Effective, Recorded, EventSource.Import);

        Assert.Equal(a, b);
    }
}
