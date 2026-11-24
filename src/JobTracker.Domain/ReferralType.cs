namespace JobTracker.Domain;

/// <summary>
/// How an application came about (the same two words as the dashboard's <c>referral_type</c> column).
/// An application with no referral type simply has null.
/// </summary>
public enum ReferralType
{
    NetworkingContact,
    ColdApply,
}
