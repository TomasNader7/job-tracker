using JobTracker.Domain;

namespace JobTracker.Data.Importing;

/// <summary>One event the importer will write. <see cref="DateKnown"/> false means the date is only a placeholder.</summary>
public sealed record PlannedEvent(ApplicationStatus? PreviousStatus, ApplicationStatus NewStatus, DateOnly Date, bool DateKnown);

public sealed record EventPlan(IReadOnlyList<PlannedEvent> Events, IReadOnlyList<string> Warnings);

/// <summary>
/// Works out which status events one tracker row becomes. The tracker knows the date applied, the current status
/// and sometimes an interview date, and nothing else. So the only real dates are the date applied and the
/// interview date; every other event is flagged DateKnown = false and takes the date of the event before it
/// (a placeholder that keeps the order valid, never a real date). Pure: no database.
/// </summary>
public static class EventPlanner
{
    public static EventPlan Plan(ImportRow row)
    {
        var events = new List<PlannedEvent>();
        var warnings = new List<string>();
        var applied = row.DateApplied;

        void Add(ApplicationStatus next, DateOnly date, bool known) =>
            events.Add(new PlannedEvent(events.Count == 0 ? null : events[^1].NewStatus, next, date, known));

        Add(row.Status == ApplicationStatus.NotStarted ? ApplicationStatus.NotStarted : ApplicationStatus.Applied, applied, true);

        switch (row.Status)
        {
            case ApplicationStatus.NotStarted:
            case ApplicationStatus.Applied:
                if (row.InterviewDate is not null)
                    warnings.Add($"Interview date ignored: the status is {row.Status}, so no interview has happened.");
                return new EventPlan(events, warnings);

            case ApplicationStatus.InterviewScheduled:
                if (row.InterviewDate is not null)
                    warnings.Add("Interview date ignored: the status is InterviewScheduled, so the interview has not happened yet.");
                Add(ApplicationStatus.InterviewScheduled, applied, false);
                return new EventPlan(events, warnings);
        }

        // Interviewed, Rejected, Withdrawn or Accepted.
        var interviewUsable = row.InterviewDate is { } d && d >= applied;
        if (row.InterviewDate is { } early && early < applied)
            warnings.Add("Interview date ignored: it is before the date applied.");

        var goesThroughInterview = row.Status is ApplicationStatus.Interviewed or ApplicationStatus.Accepted || interviewUsable;
        if (goesThroughInterview)
        {
            // The tracker never says when the interview was scheduled, so that step is a flagged placeholder.
            Add(ApplicationStatus.InterviewScheduled, applied, false);
            Add(ApplicationStatus.Interviewed, interviewUsable ? row.InterviewDate!.Value : applied, interviewUsable);
        }

        if (row.Status != ApplicationStatus.Interviewed)
            Add(row.Status, events[^1].Date, false);

        return new EventPlan(events, warnings);
    }
}
