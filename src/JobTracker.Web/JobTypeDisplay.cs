using JobTracker.Domain;

namespace JobTracker.Web;

/// <summary>
/// The words shown for each job type. They are the same nine names, in the same order, as
/// <c>data/synthetic/dim_job_type.csv</c> (a test checks this).
/// </summary>
public static class JobTypeDisplay
{
    public static string Label(JobType type) => type switch
    {
        JobType.DataAnalyst => "Data Analyst",
        JobType.BiAnalyst => "BI Analyst",
        JobType.BusinessAnalyst => "Business Analyst",
        JobType.DataScientist => "Data Scientist",
        JobType.MlAiEngineer => "ML/AI Engineer",
        JobType.DataEngineer => "Data Engineer",
        JobType.SoftwareEngineer => "Software Engineer",
        JobType.NewGradProgram => "New Grad Program",
        JobType.Other => "Other",
        _ => throw new ArgumentOutOfRangeException(nameof(type), type, "Unknown job type."),
    };

    /// <summary>For the list page: the label, or a dash when the application has no job type.</summary>
    public static string LabelOrDash(JobType? type) => type is { } t ? Label(t) : "-";
}
