namespace JobTracker.Domain;

/// <summary>
/// The seven real resume variants (the same names as <c>data/synthetic/dim_resume_variant.csv</c>
/// without its "Unspecified" row). "Unspecified" is not a value here: an application with no
/// variant simply has null, and exports map null to the word "Unspecified".
/// </summary>
public enum ResumeVariant
{
    DataAnalytics,
    BackendSwe,
    BusinessIntelligence,
    MlDataScience,
    DataEngineering,
    Automation,
    FrontendSwe,
}
