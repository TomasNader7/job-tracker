using System.ComponentModel.DataAnnotations;
using JobTracker.Data;
using JobTracker.Domain;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace JobTracker.Web.Pages;

public class AddModel(ApplicationService service, TimeProvider clock) : PageModel
{
    [BindProperty, Required, StringLength(200)]
    public string CompanyName { get; set; } = "";

    [BindProperty, Required, StringLength(200)]
    public string JobTitle { get; set; } = "";

    [BindProperty]
    public DateOnly DateApplied { get; set; }

    /// <summary>Optional. An empty choice in the dropdown binds to null.</summary>
    [BindProperty]
    public JobType? JobType { get; set; }

    public void OnGet() => DateApplied = DateOnly.FromDateTime(clock.GetLocalNow().DateTime);

    public IActionResult OnPost()
    {
        if (!ModelState.IsValid)
            return Page();

        service.AddApplication(CompanyName, JobTitle, DateApplied, JobType);
        return RedirectToPage("Index");
    }
}
