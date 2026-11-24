using JobTracker.Domain;

namespace JobTracker.Web;

/// <summary>
/// The words shown for each priority, in the same order as <c>data/synthetic/dim_priority.csv</c>
/// (a test checks this). The CSV's last row, "Unspecified", is the empty "(none)" choice in the app.
/// </summary>
public static class PriorityDisplay
{
    public static string Label(Priority priority) => priority switch
    {
        Priority.TopPriority => "Top Priority",
        Priority.MediumPriority => "Medium Priority",
        Priority.LowPriority => "Low Priority",
        _ => throw new ArgumentOutOfRangeException(nameof(priority), priority, "Unknown priority."),
    };
}
