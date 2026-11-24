using JobTracker.Domain;

namespace JobTracker.Web;

/// <summary>The words shown for each referral type, matching the dashboard's <c>referral_type</c> values (a test checks this).</summary>
public static class ReferralTypeDisplay
{
    public static string Label(ReferralType type) => type switch
    {
        ReferralType.NetworkingContact => "Networking Contact",
        ReferralType.ColdApply => "Cold Apply",
        _ => throw new ArgumentOutOfRangeException(nameof(type), type, "Unknown referral type."),
    };

    /// <summary>For the list page: the label, or a dash when the application has no referral type.</summary>
    public static string LabelOrDash(ReferralType? type) => type is { } t ? Label(t) : "-";
}
