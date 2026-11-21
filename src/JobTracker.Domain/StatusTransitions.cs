namespace JobTracker.Domain;

/// <summary>The single source of truth for which status changes are legal.</summary>
public static class StatusTransitions
{
    private static readonly IReadOnlyDictionary<ApplicationStatus, ApplicationStatus[]> Table =
        new Dictionary<ApplicationStatus, ApplicationStatus[]>
        {
            [ApplicationStatus.NotStarted] = [ApplicationStatus.Applied, ApplicationStatus.Withdrawn],
            [ApplicationStatus.Applied] =
                [ApplicationStatus.InterviewScheduled, ApplicationStatus.Rejected, ApplicationStatus.Withdrawn],
            [ApplicationStatus.InterviewScheduled] =
                [ApplicationStatus.Interviewed, ApplicationStatus.Rejected, ApplicationStatus.Withdrawn],
            [ApplicationStatus.Interviewed] =
            [
                ApplicationStatus.InterviewScheduled, ApplicationStatus.Rejected,
                ApplicationStatus.Accepted, ApplicationStatus.Withdrawn,
            ],
            // Reopen after a recruiter reversal; the only way out of Rejected.
            [ApplicationStatus.Rejected] = [ApplicationStatus.InterviewScheduled],
            [ApplicationStatus.Withdrawn] = [],
            [ApplicationStatus.Accepted] = [],
        };

    public static IReadOnlyList<ApplicationStatus> AllowedNext(ApplicationStatus from) => Table[from];

    public static bool IsAllowed(ApplicationStatus from, ApplicationStatus to) => Table[from].Contains(to);

    public static bool IsReopen(ApplicationStatus from, ApplicationStatus to) =>
        from == ApplicationStatus.Rejected && to == ApplicationStatus.InterviewScheduled;

    /// <summary>An application's first event may only start at NotStarted or Applied.</summary>
    public static bool IsAllowedAsFirst(ApplicationStatus first) =>
        first is ApplicationStatus.NotStarted or ApplicationStatus.Applied;
}
