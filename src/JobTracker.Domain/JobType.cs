namespace JobTracker.Domain;

/// <summary>
/// The fixed list of job types, the same nine values as <c>data/synthetic/dim_job_type.csv</c>
/// so the app and the Power BI model agree on names. An application's job type is optional.
/// </summary>
public enum JobType
{
    DataAnalyst,
    BiAnalyst,
    BusinessAnalyst,
    DataScientist,
    MlAiEngineer,
    DataEngineer,
    SoftwareEngineer,
    NewGradProgram,
    Other,
}
