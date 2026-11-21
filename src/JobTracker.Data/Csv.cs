using System.Text;

namespace JobTracker.Data;

/// <summary>Minimal RFC 4180 CSV reading and writing, used by the seeder and the snapshot exporter.</summary>
public static class Csv
{
    /// <summary>Reads CSV text into rows of fields. Handles quoted fields, doubled quotes, CRLF and LF.</summary>
    public static List<string[]> Parse(string text)
    {
        var rows = new List<string[]>();
        var fields = new List<string>();
        var field = new StringBuilder();
        var inQuotes = false;
        var anyContent = false;

        for (var i = 0; i < text.Length; i++)
        {
            var c = text[i];
            if (inQuotes)
            {
                if (c == '"')
                {
                    if (i + 1 < text.Length && text[i + 1] == '"') { field.Append('"'); i++; }
                    else inQuotes = false;
                }
                else field.Append(c);
                continue;
            }

            switch (c)
            {
                case '"': inQuotes = true; anyContent = true; break;
                case ',': fields.Add(field.ToString()); field.Clear(); anyContent = true; break;
                case '\r': break;
                case '\n':
                    if (anyContent || field.Length > 0) { fields.Add(field.ToString()); rows.Add(fields.ToArray()); }
                    fields.Clear(); field.Clear(); anyContent = false;
                    break;
                default: field.Append(c); anyContent = true; break;
            }
        }

        if (anyContent || field.Length > 0) { fields.Add(field.ToString()); rows.Add(fields.ToArray()); }
        return rows;
    }

    /// <summary>One field: wrapped in quotes only when it contains a comma, quote or newline. Null is empty.</summary>
    public static string Field(string? value)
    {
        if (string.IsNullOrEmpty(value)) return "";
        return value.IndexOfAny([',', '"', '\n', '\r']) >= 0
            ? "\"" + value.Replace("\"", "\"\"") + "\""
            : value;
    }

    public static string Line(IEnumerable<string?> fields) => string.Join(",", fields.Select(Field));
}
