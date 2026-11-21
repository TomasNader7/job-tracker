namespace JobTracker.Domain;

/// <summary>
/// Statuses an event can record. "No Reply" is deliberately absent: it is derived
/// (Applied for more than 30 days) by the projection, never stored.
/// </summary>
public enum ApplicationStatus
{
    NotStarted,
    Applied,
    InterviewScheduled,
    Interviewed,
    Rejected,
    Withdrawn,
    Accepted,
}
