using JobTracker.Domain;

namespace JobTracker.Web;

/// <summary>
/// The words shown for each resume variant, in the same order as <c>data/synthetic/dim_resume_variant.csv</c>
/// (a test checks this). The CSV's last row, "Unspecified", is the empty "(none)" choice in the app.
/// </summary>
public static class ResumeVariantDisplay
{
    public static string Label(ResumeVariant variant) => variant switch
    {
        ResumeVariant.DataAnalytics => "Data Analytics",
        ResumeVariant.BackendSwe => "Backend SWE",
        ResumeVariant.BusinessIntelligence => "Business Intelligence",
        ResumeVariant.MlDataScience => "ML/Data Science",
        ResumeVariant.DataEngineering => "Data Engineering",
        ResumeVariant.Automation => "Automation",
        ResumeVariant.FrontendSwe => "Frontend SWE",
        _ => throw new ArgumentOutOfRangeException(nameof(variant), variant, "Unknown resume variant."),
    };

    /// <summary>For the list page: the label, or a dash when the application has no resume variant.</summary>
    public static string LabelOrDash(ResumeVariant? variant) => variant is { } v ? Label(v) : "-";
}
