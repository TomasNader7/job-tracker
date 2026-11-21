namespace JobTracker.Tests;

/// <summary>Finds files in the repo no matter where the test runner starts.</summary>
internal static class RepoPaths
{
    public static string Root { get; } = FindRoot();

    public static string SyntheticDataDir => Path.Combine(Root, "data", "synthetic");

    private static string FindRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "JobTracker.sln")))
            dir = dir.Parent;
        return dir?.FullName ?? throw new InvalidOperationException("Could not find JobTracker.sln above the test folder.");
    }
}
