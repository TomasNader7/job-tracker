namespace JobTracker.Domain;

/// <summary>
/// How much Tomi wants a given job (the same names as <c>data/synthetic/dim_priority.csv</c>
/// without its "Unspecified" row). "Unspecified" is not a value here: an application with no
/// priority simply has null, and exports map null to the word "Unspecified".
/// </summary>
public enum Priority
{
    TopPriority,
    MediumPriority,
    LowPriority,
}
