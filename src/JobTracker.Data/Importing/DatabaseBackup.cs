using System.Globalization;
using Microsoft.Data.SqlClient;

namespace JobTracker.Data.Importing;

/// <summary>
/// Full backup of a LocalDB database to a .bak file, taken before a real import write.
/// The caller must treat any exception as "do not write".
/// </summary>
public static class DatabaseBackup
{
    /// <returns>The full path of the new backup file.</returns>
    public static string Create(string connectionString, string folder, DateTimeOffset now)
    {
        var builder = new SqlConnectionStringBuilder(connectionString);
        var database = builder.InitialCatalog;
        if (string.IsNullOrEmpty(database))
            throw new ArgumentException("The connection string must name a database.");

        Directory.CreateDirectory(folder);
        var stamp = now.UtcDateTime.ToString("yyyyMMdd'T'HHmmss'Z'", CultureInfo.InvariantCulture);
        var path = Path.Combine(Path.GetFullPath(folder), $"{database}_{stamp}.bak");
        if (File.Exists(path))
            throw new IOException($"A backup named {Path.GetFileName(path)} already exists. Not overwriting it.");

        using var connection = new SqlConnection(connectionString);
        connection.Open();
        using var command = connection.CreateCommand();
        // Database and path are parameters or bracket-quoted; INIT is safe because the file does not exist yet.
        command.CommandText = $"BACKUP DATABASE [{database.Replace("]", "]]")}] TO DISK = @path WITH INIT, CHECKSUM";
        command.Parameters.AddWithValue("@path", path);
        command.CommandTimeout = 120;
        command.ExecuteNonQuery();

        return path;
    }
}
