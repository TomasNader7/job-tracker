using JobTracker.Data;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace JobTracker.Web.Pages;

public class IndexModel(ApplicationService service) : PageModel
{
    public IReadOnlyList<ApplicationView> Applications { get; private set; } = [];

    public void OnGet()
    {
        // Newest applications first.
        Applications = service.List()
            .OrderByDescending(v => v.Application.DateApplied)
            .ThenByDescending(v => v.Application.Id)
            .ToList();
    }
}
