namespace JobTracker.Domain;

/// <summary>What the history of one application adds up to.</summary>
/// <param name="Status">Last valid stored status, or null if there is no valid history.</param>
/// <param name="IsNoReply">Derived: Applied for more than 30 days. Never stored.</param>
/// <param name="Problems">Plain-language notes about history that was ignored or inconsistent.</param>
public sealed record ProjectionResult(
    ApplicationStatus? Status,
    bool IsNoReply,
    IReadOnlyList<string> Problems);

/// <summary>
/// Turns an application's append-only event history into its current status.
/// Pass the events of ONE application (the first event decides which one; others are ignored).
/// </summary>
public static class StatusProjection
{
    public static readonly TimeSpan NoReplyAfter = TimeSpan.FromDays(30);

    public static ProjectionResult Project(IEnumerable<StatusEvent> events, DateTimeOffset asOf)
    {
        var all = events.ToList();
        var problems = new List<string>();

        if (all.Count == 0)
            return new ProjectionResult(null, false, problems);

        var appId = all[0].ApplicationId;
        var own = all.Where(e => e.ApplicationId == appId).ToDictionary(e => e.Id);

        // A valid correction removes the event it points at; the correction takes its place.
        var superseded = new HashSet<long>();
        var corrections = all.Where(e => e.SupersedesEventId is not null).OrderBy(e => e.RecordedAt).ThenBy(e => e.Id);
        foreach (var c in corrections)
        {
            if (own.ContainsKey(c.Id) && own.ContainsKey(c.SupersedesEventId!.Value))
                superseded.Add(c.SupersedesEventId.Value);
            else
                problems.Add($"Correction {c.Id} points at event {c.SupersedesEventId}, which is not in this application's history. Ignored.");
        }

        // A correction that points at a missing target must not be part of the chain.
        var ignored = all
            .Where(e => e.SupersedesEventId is not null
                        && !(own.ContainsKey(e.Id) && own.ContainsKey(e.SupersedesEventId.Value)))
            .Select(e => e.Id)
            .ToHashSet();

        var chain = own.Values
            .Where(e => !superseded.Contains(e.Id) && !ignored.Contains(e.Id))
            .OrderBy(e => e.EffectiveAt)
            .ThenBy(e => e.Id)
            .ToList();

        ApplicationStatus? current = null;
        DateTimeOffset? since = null;

        foreach (var e in chain)
        {
            if (current is null)
            {
                if (!StatusTransitions.IsAllowedAsFirst(e.NewStatus))
                {
                    problems.Add($"Event {e.Id}: an application cannot start at {e.NewStatus}. History stopped here.");
                    break;
                }
                if (e.PreviousStatus is not null)
                    problems.Add($"Event {e.Id}: first event says it came from {e.PreviousStatus}. The chain wins.");
            }
            else
            {
                if (!StatusTransitions.IsAllowed(current.Value, e.NewStatus))
                {
                    problems.Add($"Event {e.Id}: {current} to {e.NewStatus} is not allowed. History stopped here.");
                    break;
                }
                if (e.PreviousStatus != current)
                    problems.Add($"Event {e.Id}: says it came from {e.PreviousStatus?.ToString() ?? "nothing"} but the chain was at {current}. The chain wins.");
            }

            current = e.NewStatus;
            since = e.EffectiveAt;
        }

        var noReply = current == ApplicationStatus.Applied && since is not null && asOf - since.Value > NoReplyAfter;
        return new ProjectionResult(current, noReply, problems);
    }
}
