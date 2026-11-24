namespace JobTracker.Domain;

/// <summary>
/// A job application. It deliberately has no Status property: the current status is
/// projected from its <see cref="StatusEvent"/> history, never stored or overwritten.
/// </summary>
public sealed class Application
{
    public int Id { get; init; }
    public required string CompanyName { get; init; }
    public required string JobTitle { get; init; }
    public required DateOnly DateApplied { get; init; }
    public JobType? JobType { get; init; }
    public ResumeVariant? ResumeVariant { get; init; }
    public Priority? Priority { get; init; }
    public ReferralType? ReferralType { get; init; }
}
