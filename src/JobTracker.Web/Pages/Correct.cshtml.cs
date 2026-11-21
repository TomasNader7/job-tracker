using JobTracker.Data;
using JobTracker.Domain;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace JobTracker.Web.Pages;

public class CorrectModel(ApplicationService service) : PageModel
{
    public ApplicationView View { get; private set; } = null!;

    /// <summary>The wrong entry being corrected. It stays in the history; the correction replaces it.</summary>
    public StatusEvent Target { get; private set; } = null!;

    [BindProperty]
    public ApplicationStatus NewStatus { get; set; }

    [BindProperty]
    public DateOnly EffectiveDate { get; set; }

    [BindProperty]
    public string? Note { get; set; }

    public IActionResult OnGet(int id, long eventId)
    {
        if (!Load(id, eventId)) return NotFound();

        NewStatus = Target.NewStatus;
        EffectiveDate = DateOnly.FromDateTime(Target.EffectiveAt.UtcDateTime);
        return Page();
    }

    public IActionResult OnPost(int id, long eventId)
    {
        if (!Load(id, eventId)) return NotFound();

        var effectiveAt = new DateTimeOffset(EffectiveDate.ToDateTime(TimeOnly.MinValue), TimeSpan.Zero);
        var result = service.CorrectEvent(id, eventId, NewStatus, effectiveAt, Note);

        if (!result.Succeeded)
        {
            ModelState.AddModelError(string.Empty, result.Error!);
            return Page();
        }

        return RedirectToPage("Details", new { id });
    }

    private bool Load(int id, long eventId)
    {
        var view = service.Get(id);
        var target = view?.Events.SingleOrDefault(e => e.Id == eventId);

        // Only the current version of an entry can be corrected.
        if (view is null || target is null || view.IsSuperseded(target)) return false;

        View = view;
        Target = target;
        return true;
    }
}
