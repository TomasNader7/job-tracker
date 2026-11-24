using JobTracker.Domain;
using Microsoft.EntityFrameworkCore;

namespace JobTracker.Data;

/// <summary>
/// The "clerk's desk": lists which drawers (tables) exist and how each form maps to a row.
/// It does not hold any business rules.
/// </summary>
public sealed class JobTrackerDbContext(DbContextOptions<JobTrackerDbContext> options) : DbContext(options)
{
    public DbSet<Application> Applications => Set<Application>();
    public DbSet<StatusEvent> StatusEvents => Set<StatusEvent>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<StatusEvent>(e =>
        {
            // StatusEvent has read-only properties (it is never edited), so EF needs to be told
            // to map each one. It fills them in through the private constructor.
            e.HasKey(x => x.Id);
            e.Property(x => x.Id).ValueGeneratedOnAdd();
            e.Property(x => x.ApplicationId);
            // Enums are stored as words ("Applied"), not numbers, so the table is readable in Power BI.
            e.Property(x => x.PreviousStatus).HasConversion<string>().HasMaxLength(30);
            e.Property(x => x.NewStatus).HasConversion<string>().HasMaxLength(30);
            e.Property(x => x.EffectiveAt);
            e.Property(x => x.RecordedAt);
            e.Property(x => x.Source).HasConversion<string>().HasMaxLength(30);
            e.Property(x => x.SupersedesEventId);
            e.Property(x => x.Note).HasMaxLength(1000);

            // Every event must belong to a real application, and a correction must point at a real event.
            // Restrict = the database refuses to delete a row that history still points at.
            e.HasOne<Application>().WithMany().HasForeignKey(x => x.ApplicationId).OnDelete(DeleteBehavior.Restrict);
            e.HasOne<StatusEvent>().WithMany().HasForeignKey(x => x.SupersedesEventId).OnDelete(DeleteBehavior.Restrict);
            e.HasIndex(x => x.ApplicationId);
        });

        modelBuilder.Entity<Application>(a =>
        {
            a.Property(x => x.CompanyName).HasMaxLength(200);
            a.Property(x => x.JobTitle).HasMaxLength(200);
            // Optional, stored as a word ("DataAnalyst"); null for applications with no job type.
            a.Property(x => x.JobType).HasConversion<string>().HasMaxLength(30);
            // Optional, stored as a word ("BackendSwe"); null means Unspecified.
            a.Property(x => x.ResumeVariant).HasConversion<string>().HasMaxLength(30);
            // Optional, stored as a word ("TopPriority"); null means Unspecified.
            a.Property(x => x.Priority).HasConversion<string>().HasMaxLength(30);
        });
    }
}
