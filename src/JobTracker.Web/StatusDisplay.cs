using JobTracker.Data;
using JobTracker.Domain;

namespace JobTracker.Web;

/// <summary>Turns a projected status into the words shown on screen.</summary>
public static class StatusDisplay
{
    public static string Label(ApplicationView view)
    {
        if (view.Projection.IsNoReply) return "No Reply";
        return view.Projection.Status is { } s ? Label(s) : "No history";
    }

    public static string Label(ApplicationStatus status) => status switch
    {
        ApplicationStatus.NotStarted => "Not Started",
        ApplicationStatus.InterviewScheduled => "Interview Scheduled",
        _ => status.ToString(),
    };
}
