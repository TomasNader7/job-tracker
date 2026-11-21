namespace JobTracker.Domain;

public enum EventSource
{
    Manual,
    Import,
    Correction,
}

/// <summary>
/// One row of the append-only status history. Never updated or deleted: a wrong entry is
/// fixed by appending a correction that points at it through <see cref="SupersedesEventId"/>.
/// </summary>
public sealed record StatusEvent
{
    public long Id { get; }
    public int ApplicationId { get; }
    public ApplicationStatus? PreviousStatus { get; }
    public ApplicationStatus NewStatus { get; }

    /// <summary>When it really happened.</summary>
    public DateTimeOffset EffectiveAt { get; }

    /// <summary>When it was entered into the system.</summary>
    public DateTimeOffset RecordedAt { get; }

    public EventSource Source { get; }
    public long? SupersedesEventId { get; }
    public string? Note { get; }

    private StatusEvent(long id, int applicationId, ApplicationStatus? previousStatus, ApplicationStatus newStatus,
        DateTimeOffset effectiveAt, DateTimeOffset recordedAt, EventSource source,
        long? supersedesEventId, string? note)
    {
        Id = id;
        ApplicationId = applicationId;
        PreviousStatus = previousStatus;
        NewStatus = newStatus;
        EffectiveAt = effectiveAt;
        RecordedAt = recordedAt;
        Source = source;
        SupersedesEventId = supersedesEventId;
        Note = note;
    }

    public static StatusEvent Create(long id, int applicationId, ApplicationStatus? previousStatus,
        ApplicationStatus newStatus, DateTimeOffset effectiveAt, DateTimeOffset recordedAt,
        EventSource source, long? supersedesEventId = null, string? note = null)
    {
        if (supersedesEventId is not null && source != EventSource.Correction)
            throw new ArgumentException("An event that supersedes another must have source Correction.", nameof(source));

        if (supersedesEventId is null && source == EventSource.Correction)
            throw new ArgumentException("A Correction must say which event it supersedes.", nameof(supersedesEventId));

        if (supersedesEventId == id)
            throw new ArgumentException("An event cannot supersede itself.", nameof(supersedesEventId));

        if (supersedesEventId is not null && string.IsNullOrWhiteSpace(note))
            throw new ArgumentException("A correction requires a note explaining it.", nameof(note));

        return new StatusEvent(id, applicationId, previousStatus, newStatus, effectiveAt, recordedAt,
            source, supersedesEventId, note);
    }
}
