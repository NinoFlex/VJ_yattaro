using System.Windows.Forms;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace ShazamWebViewBridge;

internal static class Program
{
    internal static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true
    };
    private static readonly object OutputLock = new();

    internal static void Send(object payload)
    {
        lock (OutputLock)
        {
            try
            {
                Console.Out.WriteLine(JsonSerializer.Serialize(payload, JsonOptions));
                Console.Out.Flush();
            }
            catch (IOException) { /* Parent has exited. */ }
        }
    }

    internal static void Log(string message)
    {
        try { Console.Error.WriteLine($"{DateTimeOffset.Now:O} {message}"); }
        catch (IOException) { }
    }

    [STAThread]
    private static int Main(string[] args)
    {
        Console.InputEncoding = new UTF8Encoding(false);
        Console.OutputEncoding = new UTF8Encoding(false);
        string language = "ja-JP";
        string instanceId = "main";
        bool debug = args.Contains("--debug");
        for (int i = 0; i + 1 < args.Length; i++)
        {
            if (args[i] == "--language") language = args[++i];
            else if (args[i] == "--instance") instanceId = args[++i];
        }
        if (!Regex.IsMatch(language, @"\A[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\z"))
            language = "ja-JP";
        if (!Regex.IsMatch(instanceId, @"\A[A-Za-z0-9_-]{1,32}\z"))
            instanceId = "main";
        try
        {
            CultureInfo.DefaultThreadCurrentCulture = CultureInfo.GetCultureInfo(language);
            CultureInfo.DefaultThreadCurrentUICulture = CultureInfo.GetCultureInfo(language);
            ApplicationConfiguration.Initialize();
            Application.Run(new BridgeHost(language, debug, instanceId));
            return 0;
        }
        catch (Exception ex)
        {
            Log(ex.ToString());
            Send(new { type = "fatal", error = "WebView2 helper startup failed: " + ex.Message });
            return 1;
        }
    }
}
