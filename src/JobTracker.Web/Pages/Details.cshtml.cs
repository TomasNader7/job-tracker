using JobTracker.Data;
using JobTracker.Domain;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace JobTracker.Web.Pages;

public class DetailsModel(ApplicationService service, TimeProvider clock) : PageModel
{
    public ApplicationView View { get; private set; } = null!;

    /// <summary>The only moves the rules allow from the current status. Empty for a final status.</summary>
    public IReadOnlyList<ApplicationStatus> AllowedNext { get; private set; } = [];

    [BindProperty]
    public ApplicationStatus NewStatus { get; set; }

    [BindProperty]
    public DateOnly EffectiveDate { get; set; }

    [BindProperty]
    public string? Note { get; set; }

    public IActionResult OnGet(int id)
    {
        if (!Load(id)) return NotFound();
        EffectiveDate = DateOnly.FromDateTime(clock.GetLocalNow().DateTime);
        return Page();
    }

    public IActionResult OnPost(int id)
    {
        if (!Load(id)) return NotFound();

        // Dates are whole days (midnight UTC), the same as the date applied.
        var effectiveAt = new DateTimeOffset(EffectiveDate.ToDateTime(TimeOnly.MinValue), TimeSpan.Zero);
        var result = service.RecordStatusChange(id, NewStatus, effectiveAt, Note);

        if (!result.Succeeded)
        {
            ModelState.AddModelError(string.Empty, result.Error!);
            return Page();
        }

        return RedirectToPage(new { id });
    }

    private bool Load(int id)
    {
        var view = service.Get(id);
        if (view is null) return false;

        View = view;
        AllowedNext = view.Projection.Status is { } s ? StatusTransitions.AllowedNext(s) : [];
        return true;
    }
}
