using System.Drawing;
using System.Windows.Forms;
using System.Diagnostics;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace ShazamWebViewBridge;

internal sealed record Candidate(
    string Title = "", string Artist = "", string ShazamTrackId = "",
    string AppleTrackId = "", string AppleMusicUrl = "",
    string Url = "", string Evidence = "");

internal sealed class BridgeHost : Form
{
    private readonly string _language;
    private readonly bool _debug;
    private readonly string _instanceId;
    private readonly WebView2 _webView = new() { Dock = DockStyle.Fill };
    private readonly CancellationTokenSource _lifetime = new();
    private bool _ready;
    private bool _busy;
    private string? _cycle;
    private Candidate? _best;
    private long _candidateTime;
    private string? _audioError;
    private bool _audioStarted;
    private bool _definiteNoMatch;
    private string Home => "https://www.shazam.com/" + _language.ToLowerInvariant();

    public BridgeHost(string language, bool debug, string instanceId)
    {
        _language = language;
        _debug = debug;
        _instanceId = instanceId;
        Text = "VJ_yattaro - Shazam WebView2 diagnostics";
        ShowInTaskbar = debug;
        StartPosition = FormStartPosition.Manual;
        FormBorderStyle = debug ? FormBorderStyle.Sizable : FormBorderStyle.None;
        ClientSize = new Size(1000, 720);
        Location = debug ? new Point(80, 80) : new Point(-20000, -20000);
        Controls.Add(_webView);
        Shown += OnShown;
        FormClosing += (_, _) => _lifetime.Cancel();
    }

    protected override bool ShowWithoutActivation => !_debug;
    protected override CreateParams CreateParams
    {
        get
        {
            var p = base.CreateParams;
            if (!_debug) p.ExStyle |= 0x08000000 | 0x00000080; // NOACTIVATE | TOOLWINDOW
            return p;
        }
    }

    private static string Script(string name)
    {
        using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(
            "ShazamWebViewBridge.Scripts." + name) ?? throw new FileNotFoundException(name);
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd();
    }

    private async void OnShown(object? sender, EventArgs e)
    {
        _ = Task.Run(ReadCommandsAsync);
        try
        {
            var folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "VJ_yattaro", "ShazamWebView2", "profiles", _language.ToLowerInvariant(), _instanceId);
            // A dedicated app profile, not the user's Edge/Chrome/ShazamWatch profile.
            var environment = await CoreWebView2Environment.CreateAsync(null, folder,
                new CoreWebView2EnvironmentOptions { Language = _language });
            await _webView.EnsureCoreWebView2Async(environment);
            var core = _webView.CoreWebView2;
            core.Settings.AreDefaultContextMenusEnabled = _debug;
            core.Settings.AreDevToolsEnabled = _debug;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.IsZoomControlEnabled = false;
            core.Settings.IsWebMessageEnabled = true;
            core.IsMuted = true; // No microphone loopback to the user's speakers.
            core.PermissionRequested += (_, args) =>
            {
                // The selected microphone is already captured by Python. The helper
                // must not open some other physical microphone or camera by accident.
                args.State = CoreWebView2PermissionState.Deny;
                args.Handled = true;
            };
            core.NewWindowRequested += (_, args) => args.Handled = true;
            core.DownloadStarting += (_, args) => args.Cancel = true;
            core.WebMessageReceived += OnWebMessage;
            core.WebResourceResponseReceived += OnWebResourceResponseReceived;
            core.NavigationStarting += OnNavigationStarting;
            core.SourceChanged += (_, _) => CaptureRoute(core.Source);
            core.NavigationCompleted += (_, args) =>
                Program.Log($"Navigation success={args.IsSuccess} status={args.HttpStatusCode} uri={core.Source}");
            core.ProcessFailed += (_, args) =>
            {
                Program.Send(new { type = "fatal", error = "WebView2 process failed: " + args.ProcessFailedKind });
                Close();
            };
            await core.AddScriptToExecuteOnDocumentCreatedAsync(Script("audio_bridge.js"));
            await core.AddScriptToExecuteOnDocumentCreatedAsync(Script("result_observer.js"));
            await NavigateHomeAsync(_lifetime.Token);
            _ready = true;
            Program.Log($"Ready; WebView2={environment.BrowserVersionString}; locale={_language}; instance={_instanceId}; input=app-WAV");
            Program.Send(new { type = "ready", protocol = 1, version = "1.2.5" });
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            Program.Log(ex.ToString());
            Program.Send(new { type = "fatal", error = "WebView2 initialization failed. " +
                "Check the Microsoft Edge WebView2 Runtime and Internet connection. " + ex.Message });
            Close();
        }
    }

    private async Task ReadCommandsAsync()
    {
        try
        {
            while (!_lifetime.IsCancellationRequested)
            {
                var line = await Console.In.ReadLineAsync(_lifetime.Token);
                if (line is null) break; // Parent closed or exited.
                if (line.Length > 2100000)
                {
                    Program.Send(new { type = "fatal", error = "IPC request exceeds size limit" });
                    break;
                }
                string copy = line;
                if (IsDisposed || !IsHandleCreated) break;
                BeginInvoke(new Action(() => _ = HandleCommandAsync(copy)));
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { Program.Log("IPC reader: " + ex.Message); }
        finally
        {
            if (!IsDisposed && IsHandleCreated)
                try { BeginInvoke(new Action(Close)); } catch (InvalidOperationException) { }
        }
    }

    private async Task HandleCommandAsync(string line)
    {
        string id = "";
        bool ownsCycle = false;
        bool shouldSend = false;
        Candidate? result = null;
        string error = "";
        try
        {
            using var doc = JsonDocument.Parse(line);
            var root = doc.RootElement;
            string type = ReadString(root, "type");
            id = ReadString(root, "id");
            if (type == "shutdown") { Close(); return; }
            if (type != "recognize" || !Regex.IsMatch(id, @"\A[a-f0-9]{32}\z"))
                throw new InvalidOperationException("Invalid recognition command");
            if (!_ready || _busy) throw new InvalidOperationException("Shazam helper is not ready or is busy");
            var audio = ReadString(root, "wavBase64");
            if (audio.Length < 60 || audio.Length > 2000000)
                throw new InvalidOperationException("Invalid WAV payload length");
            _busy = ownsCycle = true;
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(_lifetime.Token);
            deadline.CancelAfter(TimeSpan.FromSeconds(75));
            result = await RecognizeAsync(id, audio, deadline.Token);
            shouldSend = true;
        }
        catch (OperationCanceledException) when (!_lifetime.IsCancellationRequested)
        {
            error = "Shazam recognition exceeded its 75 second deadline";
            shouldSend = true;
        }
        catch (OperationCanceledException) { return; }
        catch (Exception ex)
        {
            Program.Log("Recognition failed: " + ex.Message);
            error = ex.Message;
            shouldSend = !string.IsNullOrWhiteSpace(id);
        }
        finally
        {
            if (ownsCycle)
            {
                _cycle = null;
                try
                {
                    if (!IsDisposed && _webView.CoreWebView2 is not null)
                        await _webView.CoreWebView2.ExecuteScriptAsync(
                            "window.__vjResults?.stop(); window.__vjAudioBridge?.stop();");
                }
                catch (Exception) { }
                // IMPORTANT: mark the helper idle before notifying Python. Python queues
                // the next recognition immediately when it receives this result.
                _busy = false;
            }
        }

        if (!shouldSend || _lifetime.IsCancellationRequested) return;
        Program.Send(new { type = "result", id,
            title = result?.Title ?? "", artist = result?.Artist ?? "",
            shazamTrackId = result?.ShazamTrackId ?? "",
            appleTrackId = result?.AppleTrackId ?? "", appleMusicUrl = result?.AppleMusicUrl ?? "",
            url = result?.Url ?? "", source = result?.Evidence ?? "no-match", error });
    }

    private async Task<Candidate?> RecognizeAsync(string id, string audio, CancellationToken token)
    {
        // Every attempt starts from, or reuses when already ready, the official Shazam home page.
        // The app-supplied recording remains the only audio source; WebView2 never opens a native mic.
        _cycle = null;
        var button = await PrepareHomeAsync(token);
        var baselineTrackIds = await GetAppleTrackIdsAsync(token);
        _best = null;
        _audioError = null;
        _audioStarted = false;
        _definiteNoMatch = false;
        _cycle = id;
        var load = "(async()=>{if(!window.__vjAudioBridge||!window.__vjResults)" +
            "throw new Error('Shazam page audio bridge unavailable');" +
            "await window.__vjAudioBridge.load(" + JsonSerializer.Serialize(audio) + "," +
            JsonSerializer.Serialize(id) + ");window.__vjResults.arm(" + JsonSerializer.Serialize(id) +
            ");return true;})()";
        await EvaluateAsync(load, token);
        button = await WaitForButtonAsync(token);
        Program.Log($"Recognition start id={id} label={button.Label} baselineAppleIds={baselineTrackIds.Count}");
        await ClickAsync(button.X, button.Y, token);

        var watch = Stopwatch.StartNew();
        string routeEvidenceAttemptedFor = "";
        while (watch.Elapsed < TimeSpan.FromSeconds(15))
        {
            token.ThrowIfCancellationRequested();
            if (_audioError is not null) throw new InvalidOperationException(_audioError);
            var best = _best;
            if (best is not null)
            {
                // The Shazam route ID is NOT an Apple ID. When a route appears, let the
                // exact live result page render briefly and collect its Japanese title,
                // artist and (when present) its real Apple Music ?i= track link.
                if (!string.IsNullOrWhiteSpace(best.ShazamTrackId) &&
                    !string.Equals(routeEvidenceAttemptedFor, best.ShazamTrackId, StringComparison.Ordinal))
                {
                    routeEvidenceAttemptedFor = best.ShazamTrackId;
                    var evidence = await CaptureRouteEvidenceAsync(best, baselineTrackIds, token);
                    if (evidence is not null)
                    {
                        Program.Log($"Route evidence title={evidence.Title} artist={evidence.Artist} " +
                            $"shazamTrackId={evidence.ShazamTrackId} appleTrackId={evidence.AppleTrackId} source={evidence.Evidence}");
                        Accept(evidence);
                    }
                    best = _best;
                    if (best is not null && !string.IsNullOrWhiteSpace(best.ShazamTrackId))
                    {
                        Program.Log($"Recognized route source={best.Evidence} shazamTrackId={best.ShazamTrackId} " +
                            $"appleTrackId={best.AppleTrackId} title={best.Title} artist={best.Artist}");
                        return best;
                    }
                }

                bool complete = !string.IsNullOrWhiteSpace(best.Title) && !string.IsNullOrWhiteSpace(best.Artist);
                var candidateAge = Stopwatch.GetElapsedTime(_candidateTime);
                var fastIdentity = !string.IsNullOrWhiteSpace(best.AppleTrackId);
                var networkText = (best.Evidence ?? "").StartsWith("recognition-network", StringComparison.OrdinalIgnoreCase) ||
                    (best.Evidence ?? "").StartsWith("recognition-response", StringComparison.OrdinalIgnoreCase);
                var grace = fastIdentity ? TimeSpan.FromMilliseconds(250)
                    : networkText ? TimeSpan.FromMilliseconds(650)
                    : TimeSpan.FromMilliseconds(300);
                if (complete && candidateAge > grace)
                {
                    // Network text without an Apple ID gets a slightly longer grace so a /ja-jp
                    // route can still contribute a Japanese title. Exact Apple identity returns faster.
                    Program.Log($"Recognized source={best.Evidence} shazamTrackId={best.ShazamTrackId} " +
                        $"appleTrackId={best.AppleTrackId} title={best.Title} artist={best.Artist}");
                    return best;
                }

                // A real Apple track link introduced by this recognition is sufficient
                // identity evidence even if the visible text has not painted yet.
                if (fastIdentity && candidateAge > TimeSpan.FromMilliseconds(250))
                    return best;
            }

            // A no-match signal must not erase a route/candidate that was already captured.
            // This matters when Shazam briefly navigates through an error state after a match.
            if (_definiteNoMatch && _best is null)
            {
                Program.Log("Recognition response/page reported no match; retrying immediately.");
                return null;
            }

            try { await _webView.CoreWebView2.ExecuteScriptAsync("window.__vjResults?.scan();"); }
            catch (Exception) { /* Navigation may be in progress. Route capture remains active. */ }
            await Task.Delay(100, token);
            if (!_audioStarted && watch.Elapsed > TimeSpan.FromSeconds(10))
                throw new InvalidOperationException("The Shazam button did not request the supplied audio. Use VJ_SHAZAM_DEBUG=1 to inspect the page/consent dialog.");
        }
        Program.Log("Recognition finished with no usable match (15 second safety deadline).");
        return null;
    }

    private async Task<HashSet<string>> GetAppleTrackIdsAsync(CancellationToken token)
    {
        var result = new HashSet<string>(StringComparer.Ordinal);
        try
        {
            var json = await _webView.CoreWebView2.ExecuteScriptAsync(Script("collect_apple_ids.js"))
                .WaitAsync(TimeSpan.FromSeconds(5), token);
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.ValueKind == JsonValueKind.Array)
                foreach (var item in doc.RootElement.EnumerateArray())
                {
                    var value = item.GetString();
                    if (!string.IsNullOrWhiteSpace(value)) result.Add(value);
                }
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            Program.Log("Could not collect Apple Music baseline: " + ex.Message);
        }
        return result;
    }

    private async Task<Candidate?> CaptureRouteEvidenceAsync(
        Candidate routeCandidate, IReadOnlyCollection<string> baselineTrackIds, CancellationToken token)
    {
        if (string.IsNullOrWhiteSpace(routeCandidate.ShazamTrackId)) return null;
        var deadline = Stopwatch.StartNew();
        Candidate? best = null;
        var script = Script("route_evidence.js").Replace(
            "__BASELINE_TRACK_IDS__", JsonSerializer.Serialize(baselineTrackIds), StringComparison.Ordinal);

        while (deadline.Elapsed < TimeSpan.FromMilliseconds(1000))
        {
            token.ThrowIfCancellationRequested();
            try
            {
                if (TryParseShazamRoute(_webView.CoreWebView2.Source, out var routeId) &&
                    string.Equals(routeId, routeCandidate.ShazamTrackId, StringComparison.Ordinal))
                {
                    var json = await _webView.CoreWebView2.ExecuteScriptAsync(script);
                    if (!string.IsNullOrWhiteSpace(json) && json != "null")
                    {
                        var candidate = JsonSerializer.Deserialize<Candidate>(json, Program.JsonOptions);
                        if (candidate is not null &&
                            string.Equals(candidate.ShazamTrackId, routeCandidate.ShazamTrackId, StringComparison.Ordinal))
                        {
                            best = Better(best, candidate);
                            if (!string.IsNullOrWhiteSpace(candidate.AppleTrackId) ||
                                (candidate.Evidence == "track-heading" &&
                                 !string.IsNullOrWhiteSpace(candidate.Title) &&
                                 !string.IsNullOrWhiteSpace(candidate.Artist)))
                                return best;
                        }
                    }
                }
            }
            catch (JsonException ex) { Program.Log("Route evidence JSON ignored: " + ex.Message); }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                // During SPA/document navigation ExecuteScriptAsync can briefly fail.
                Program.Log("Route evidence poll unavailable: " + ex.Message);
            }
            await Task.Delay(75, token);
        }
        return best;
    }

    private bool IsHomeUrl(string? value)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var current) ||
            !Uri.TryCreate(Home, UriKind.Absolute, out var home)) return false;
        return string.Equals(current.Scheme, home.Scheme, StringComparison.OrdinalIgnoreCase) &&
            string.Equals(current.Host, home.Host, StringComparison.OrdinalIgnoreCase) &&
            string.Equals(current.AbsolutePath.TrimEnd('/'), home.AbsolutePath.TrimEnd('/'), StringComparison.OrdinalIgnoreCase);
    }

    private async Task<Button?> FindButtonOnceAsync(CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        try
        {
            var json = await _webView.CoreWebView2.ExecuteScriptAsync(Script("find_button.js"));
            if (json == "null" || string.IsNullOrWhiteSpace(json)) return null;
            using var doc = JsonDocument.Parse(json);
            var r = doc.RootElement;
            if (r.ValueKind == JsonValueKind.Object && r.TryGetProperty("x", out var x))
                return new Button(x.GetDouble(), r.GetProperty("y").GetDouble(), ReadString(r, "label"));
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            Program.Log("Fast home readiness check unavailable: " + ex.Message);
        }
        return null;
    }

    private async Task<Button> PrepareHomeAsync(CancellationToken token)
    {
        // A no-match normally leaves Shazam on the homepage. Re-navigating the same
        // page costs ~0.5-0.7 s per retry, so reuse it when the recognition button is
        // already visible. Result/detail pages still navigate home normally.
        if (IsHomeUrl(_webView.CoreWebView2.Source))
        {
            var existing = await FindButtonOnceAsync(token);
            if (existing is not null) return existing.Value;
        }
        await NavigateHomeAsync(token);
        return await WaitForButtonAsync(token);
    }

    private async Task NavigateHomeAsync(CancellationToken token)
    {
        var core = _webView.CoreWebView2;
        var done = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        ulong? navigationId = null;
        void OnStarting(object? sender, CoreWebView2NavigationStartingEventArgs args)
        {
            if (navigationId is null && string.Equals(args.Uri.TrimEnd('/'), Home.TrimEnd('/'),
                    StringComparison.OrdinalIgnoreCase))
                navigationId = args.NavigationId;
        }
        void OnCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs args)
        {
            // A cancelled song navigation from the preceding cycle can complete late.
            // It must not satisfy/fail this new homepage navigation waiter.
            if (navigationId == args.NavigationId)
                done.TrySetResult(args.IsSuccess && args.HttpStatusCode < 400);
        }
        core.NavigationStarting += OnStarting;
        core.NavigationCompleted += OnCompleted;
        try
        {
            core.Navigate(Home);
            if (!await done.Task.WaitAsync(TimeSpan.FromSeconds(25), token))
                throw new InvalidOperationException("Shazam homepage failed to load");
        }
        finally
        {
            core.NavigationStarting -= OnStarting;
            core.NavigationCompleted -= OnCompleted;
        }
    }

    private readonly record struct Button(double X, double Y, string Label);
    private async Task<Button> WaitForButtonAsync(CancellationToken token)
    {
        var deadline = Stopwatch.StartNew();
        while (deadline.Elapsed < TimeSpan.FromSeconds(12))
        {
            token.ThrowIfCancellationRequested();
            var json = await _webView.CoreWebView2.ExecuteScriptAsync(Script("find_button.js"));
            if (json != "null" && !string.IsNullOrWhiteSpace(json))
            {
                using var doc = JsonDocument.Parse(json);
                var r = doc.RootElement;
                if (r.ValueKind == JsonValueKind.Object && r.TryGetProperty("x", out var x))
                    return new Button(x.GetDouble(), r.GetProperty("y").GetDouble(), ReadString(r, "label"));
            }
            await Task.Delay(300, token);
        }
        // No automatic acceptance of site terms/cookies on the user's behalf.
        throw new InvalidOperationException("Shazam recognition button not found. Enable VJ_SHAZAM_DEBUG=1 and check the page or consent screen.");
    }

    private async Task ClickAsync(double x, double y, CancellationToken token)
    {
        var core = _webView.CoreWebView2;
        foreach (var type in new[] { "mouseMoved", "mousePressed", "mouseReleased" })
        {
            var data = new { type, x, y, button = type == "mouseMoved" ? "none" : "left",
                buttons = type == "mousePressed" ? 1 : 0, clickCount = type == "mouseMoved" ? 0 : 1 };
            await core.CallDevToolsProtocolMethodAsync("Input.dispatchMouseEvent", JsonSerializer.Serialize(data));
            if (type == "mousePressed") await Task.Delay(50, token);
        }
    }

    private async Task EvaluateAsync(string expression, CancellationToken token)
    {
        var parameters = JsonSerializer.Serialize(new { expression, awaitPromise = true,
            returnByValue = true, userGesture = true });
        var json = await _webView.CoreWebView2.CallDevToolsProtocolMethodAsync("Runtime.evaluate", parameters)
            .WaitAsync(TimeSpan.FromSeconds(12), token);
        using var doc = JsonDocument.Parse(json);
        if (doc.RootElement.TryGetProperty("exceptionDetails", out var error))
            throw new InvalidOperationException("WebView2 audio initialization failed: " + error.ToString());
    }

    private static bool IsShazam(string value) => Uri.TryCreate(value, UriKind.Absolute, out var u)
        && u.Scheme == "https" && (u.Host == "www.shazam.com" || u.Host == "shazam.com");

    private void OnNavigationStarting(object? sender, CoreWebView2NavigationStartingEventArgs args)
    {
        if (!IsShazam(args.Uri)) { args.Cancel = true; return; }
        // Do NOT cancel the recognized song/track route. v1.2 briefly lets the exact
        // live result render so Japanese title/artist and the real Apple Music link
        // can be collected before the next recognition returns home.
        CaptureRoute(args.Uri);
    }

    private void CaptureRoute(string url)
    {
        if (_cycle is null || !IsShazam(url) || !TryParseShazamRoute(url, out var id)) return;
        Accept(new Candidate(ShazamTrackId: id, Url: url, Evidence: "shazam-route"));
    }

    private static bool TryParseShazamRoute(string? url, out string id)
    {
        id = "";
        if (string.IsNullOrWhiteSpace(url) || !IsShazam(url)) return false;
        var uri = new Uri(url);
        var m = Regex.Match(uri.AbsolutePath,
            @"\A/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:song|track)/(\d{6,20})(?:/|$)",
            RegexOptions.IgnoreCase);
        if (!m.Success) return false;
        id = m.Groups[1].Value;
        return true;
    }

    private void OnWebMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        if (_cycle is null || !IsShazam(args.Source)) return;
        try
        {
            using var doc = JsonDocument.Parse(args.WebMessageAsJson);
            var root = doc.RootElement;
            if (ReadString(root, "source") != "VJShazam" || ReadString(root, "id") != _cycle) return;
            switch (ReadString(root, "type"))
            {
                case "candidate":
                    var c = JsonSerializer.Deserialize<Candidate>(args.WebMessageAsJson, Program.JsonOptions);
                    if (c is not null) Accept(c);
                    break;
                case "audio-stream-started":
                    _audioStarted = true;
                    Program.Log("Official page requested app-supplied audio; stream running.");
                    break;
                case "audio-error":
                    _audioError = ReadString(root, "error");
                    break;
                case "audio-loaded":
                    Program.Log("App recording decoded by WebView2.");
                    break;
                case "no-match":
                    _definiteNoMatch = true;
                    Program.Log("Official Shazam recognition response reported no match.");
                    break;
            }
        }
        catch (JsonException ex) { Program.Log("Ignored invalid web message: " + ex.Message); }
    }

    private async void OnWebResourceResponseReceived(
        object? sender, CoreWebView2WebResourceResponseReceivedEventArgs args)
    {
        // Observe the response that the official Shazam page itself requested. This
        // does not make a second recognition request; it only avoids waiting for SPA
        // rendering when the /tag response is already available in WebView2.
        var cycle = _cycle;
        if (string.IsNullOrWhiteSpace(cycle)) return;
        try
        {
            if (!Uri.TryCreate(args.Request.Uri, UriKind.Absolute, out var uri)) return;
            bool shazamHost = string.Equals(uri.Host, "shazam.com", StringComparison.OrdinalIgnoreCase) ||
                uri.Host.EndsWith(".shazam.com", StringComparison.OrdinalIgnoreCase);
            if (!shazamHost || !uri.AbsolutePath.Contains("/tag/", StringComparison.OrdinalIgnoreCase)) return;
            if (args.Response.StatusCode < 200 || args.Response.StatusCode >= 300) return;

            using var stream = await args.Response.GetContentAsync();
            using var reader = new StreamReader(stream, Encoding.UTF8, true, 8192, leaveOpen: false);
            var text = await reader.ReadToEndAsync();
            if (text.Length == 0 || text.Length > 2_000_000 || _cycle != cycle) return;
            using var doc = JsonDocument.Parse(text);
            int budget = 500;
            var candidate = FindRecognitionCandidate(doc.RootElement, ref budget);
            if (_cycle != cycle) return;
            if (candidate is not null)
            {
                Accept(candidate);
                Program.Log($"Recognition network response captured title={candidate.Title} artist={candidate.Artist} " +
                    $"shazamTrackId={candidate.ShazamTrackId} appleTrackId={candidate.AppleTrackId}");
                return;
            }

            budget = 500;
            if (ContainsEmptyRecognitionMatches(doc.RootElement, ref budget))
            {
                _definiteNoMatch = true;
                Program.Log("Official Shazam /tag network response reported no match.");
            }
        }
        catch (JsonException ex) { Program.Log("Recognition network JSON ignored: " + ex.Message); }
        catch (Exception ex)
        {
            // Network observation is a latency optimization only; DOM/route capture
            // remains authoritative if response content cannot be inspected.
            Program.Log("Recognition network observer unavailable: " + ex.Message);
        }
    }

    private static Candidate? FindRecognitionCandidate(JsonElement node, ref int budget)
    {
        if (--budget <= 0) return null;
        if (node.ValueKind == JsonValueKind.Object)
        {
            if (node.TryGetProperty("matches", out var matches) &&
                matches.ValueKind == JsonValueKind.Array && matches.GetArrayLength() > 0 &&
                node.TryGetProperty("track", out var track) && track.ValueKind == JsonValueKind.Object)
            {
                var title = ReadString(track, "title");
                var artist = ReadString(track, "subtitle");
                if (string.IsNullOrWhiteSpace(artist)) artist = ReadString(track, "artist");
                var trackUrl = ReadString(track, "url");
                string shazamTrackId = "";
                TryParseShazamRoute(trackUrl, out shazamTrackId);
                int appleBudget = 300;
                var appleUrl = FindAppleMusicUrl(track, ref appleBudget);
                var appleTrackId = AppleTrackIdFromUrl(appleUrl);
                if ((!string.IsNullOrWhiteSpace(title) && !string.IsNullOrWhiteSpace(artist)) ||
                    !string.IsNullOrWhiteSpace(shazamTrackId) || !string.IsNullOrWhiteSpace(appleTrackId))
                    return new Candidate(title, artist, shazamTrackId, appleTrackId, appleUrl,
                        string.IsNullOrWhiteSpace(trackUrl) ? "" : trackUrl, "recognition-network");
            }
            foreach (var property in node.EnumerateObject())
            {
                var found = FindRecognitionCandidate(property.Value, ref budget);
                if (found is not null) return found;
                if (budget <= 0) break;
            }
        }
        else if (node.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in node.EnumerateArray())
            {
                var found = FindRecognitionCandidate(item, ref budget);
                if (found is not null) return found;
                if (budget <= 0) break;
            }
        }
        return null;
    }

    private static bool ContainsEmptyRecognitionMatches(JsonElement node, ref int budget)
    {
        if (--budget <= 0) return false;
        if (node.ValueKind == JsonValueKind.Object)
        {
            if (node.TryGetProperty("matches", out var matches) &&
                matches.ValueKind == JsonValueKind.Array && matches.GetArrayLength() == 0 &&
                (!node.TryGetProperty("track", out var track) || track.ValueKind == JsonValueKind.Null))
                return true;
            foreach (var property in node.EnumerateObject())
                if (ContainsEmptyRecognitionMatches(property.Value, ref budget)) return true;
        }
        else if (node.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in node.EnumerateArray())
                if (ContainsEmptyRecognitionMatches(item, ref budget)) return true;
        }
        return false;
    }

    private static string FindAppleMusicUrl(JsonElement node, ref int budget)
    {
        if (--budget <= 0) return "";
        if (node.ValueKind == JsonValueKind.String)
        {
            var value = node.GetString() ?? "";
            return string.IsNullOrWhiteSpace(AppleTrackIdFromUrl(value)) ? "" : value;
        }
        if (node.ValueKind == JsonValueKind.Object)
        {
            foreach (var property in node.EnumerateObject())
            {
                var found = FindAppleMusicUrl(property.Value, ref budget);
                if (!string.IsNullOrWhiteSpace(found)) return found;
                if (budget <= 0) break;
            }
        }
        else if (node.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in node.EnumerateArray())
            {
                var found = FindAppleMusicUrl(item, ref budget);
                if (!string.IsNullOrWhiteSpace(found)) return found;
                if (budget <= 0) break;
            }
        }
        return "";
    }

    private static string AppleTrackIdFromUrl(string? value)
    {
        if (!Uri.TryCreate(value, UriKind.Absolute, out var uri) ||
            !(string.Equals(uri.Host, "music.apple.com", StringComparison.OrdinalIgnoreCase) ||
              string.Equals(uri.Host, "itunes.apple.com", StringComparison.OrdinalIgnoreCase))) return "";
        var query = uri.Query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries);
        foreach (var part in query)
        {
            var pair = part.Split('=', 2);
            if (pair.Length == 2 && pair[0] == "i")
            {
                var id = Uri.UnescapeDataString(pair[1]);
                if (Regex.IsMatch(id, @"\A[0-9]{6,20}\z")) return id;
            }
        }
        var match = Regex.Match(uri.AbsolutePath, @"/song/[^/]+/(\d{6,20})(?:/|$)", RegexOptions.IgnoreCase);
        return match.Success ? match.Groups[1].Value : "";
    }

    private static int Rank(Candidate c)
    {
        var evidence = c.Evidence ?? "";
        int rank = evidence.StartsWith("track-heading", StringComparison.OrdinalIgnoreCase) ? 180
            : evidence.StartsWith("dialog-heading", StringComparison.OrdinalIgnoreCase) ? 170
            : evidence.StartsWith("recognition-network", StringComparison.OrdinalIgnoreCase) ? 190
            : evidence.StartsWith("recognition-response", StringComparison.OrdinalIgnoreCase) ? 160
            : evidence.StartsWith("new-result-heading", StringComparison.OrdinalIgnoreCase) ? 150
            : evidence.StartsWith("result-region", StringComparison.OrdinalIgnoreCase) ? 140
            : evidence.StartsWith("jsonld", StringComparison.OrdinalIgnoreCase) ? 130
            : 60;
        if (!string.IsNullOrWhiteSpace(c.AppleTrackId)) rank += 25;
        if (!string.IsNullOrWhiteSpace(c.Title)) rank += 10;
        if (!string.IsNullOrWhiteSpace(c.Artist)) rank += 10;
        return rank;
    }

    private static Candidate Better(Candidate? current, Candidate candidate)
        => current is null || Rank(candidate) > Rank(current) ? candidate : current;

    private static bool SameKnownIdentity(Candidate a, Candidate b)
    {
        if (!string.IsNullOrWhiteSpace(a.ShazamTrackId) && !string.IsNullOrWhiteSpace(b.ShazamTrackId))
            return string.Equals(a.ShazamTrackId, b.ShazamTrackId, StringComparison.Ordinal);
        if (!string.IsNullOrWhiteSpace(a.AppleTrackId) && !string.IsNullOrWhiteSpace(b.AppleTrackId))
            return string.Equals(a.AppleTrackId, b.AppleTrackId, StringComparison.Ordinal);
        return false;
    }

    private void Accept(Candidate value)
    {
        bool validAppleId = Regex.IsMatch(value.AppleTrackId ?? "", @"\A[0-9]{6,20}\z");
        bool validShazamId = Regex.IsMatch(value.ShazamTrackId ?? "", @"\A[0-9]{6,20}\z");
        var title = ValidText(value.Title) ? value.Title : "";
        var artist = ValidText(value.Artist) ? value.Artist : "";
        if (!validAppleId && !validShazamId && (string.IsNullOrWhiteSpace(title) || string.IsNullOrWhiteSpace(artist)))
            return;

        value = value with
        {
            Title = title, Artist = artist,
            AppleTrackId = validAppleId ? value.AppleTrackId : "",
            ShazamTrackId = validShazamId ? value.ShazamTrackId : ""
        };

        if (_best is not null)
        {
            var current = _best;
            bool same = SameKnownIdentity(current, value);

            // Route capture can arrive just after the exact recognition response. Bind the
            // request-scoped response text to that route, but never reinterpret the route ID
            // as an Apple ID.
            if (!string.IsNullOrWhiteSpace(value.ShazamTrackId) &&
                string.IsNullOrWhiteSpace(current.ShazamTrackId) &&
                !string.IsNullOrWhiteSpace(current.Title) && !string.IsNullOrWhiteSpace(current.Artist) &&
                Rank(current) >= 140)
            {
                current = current with
                {
                    ShazamTrackId = value.ShazamTrackId,
                    Url = string.IsNullOrWhiteSpace(value.Url) ? current.Url : value.Url,
                    Evidence = current.Evidence + "+shazam-route"
                };
                _best = current;
                _candidateTime = Stopwatch.GetTimestamp();
                same = true;
            }

            if (Rank(value) > Rank(_best))
            {
                if (same)
                {
                    value = value with
                    {
                        Title = string.IsNullOrWhiteSpace(value.Title) ? _best.Title : value.Title,
                        Artist = string.IsNullOrWhiteSpace(value.Artist) ? _best.Artist : value.Artist,
                        ShazamTrackId = string.IsNullOrWhiteSpace(value.ShazamTrackId) ? _best.ShazamTrackId : value.ShazamTrackId,
                        AppleTrackId = string.IsNullOrWhiteSpace(value.AppleTrackId) ? _best.AppleTrackId : value.AppleTrackId,
                        AppleMusicUrl = string.IsNullOrWhiteSpace(value.AppleMusicUrl) ? _best.AppleMusicUrl : value.AppleMusicUrl,
                        Url = string.IsNullOrWhiteSpace(value.Url) ? _best.Url : value.Url
                    };
                }
                _best = value;
                _candidateTime = Stopwatch.GetTimestamp();
                return;
            }

            // Same identity: retain stronger text but fill exact IDs/links that arrive later.
            if (same)
            {
                var merged = _best with
                {
                    ShazamTrackId = string.IsNullOrWhiteSpace(_best.ShazamTrackId) ? value.ShazamTrackId : _best.ShazamTrackId,
                    AppleTrackId = string.IsNullOrWhiteSpace(_best.AppleTrackId) ? value.AppleTrackId : _best.AppleTrackId,
                    AppleMusicUrl = string.IsNullOrWhiteSpace(_best.AppleMusicUrl) ? value.AppleMusicUrl : _best.AppleMusicUrl,
                    Title = string.IsNullOrWhiteSpace(_best.Title) ? value.Title : _best.Title,
                    Artist = string.IsNullOrWhiteSpace(_best.Artist) ? value.Artist : _best.Artist,
                    Url = string.IsNullOrWhiteSpace(_best.Url) ? value.Url : _best.Url
                };
                _best = merged;
            }
            return;
        }

        _best = value;
        _candidateTime = Stopwatch.GetTimestamp();
    }

    private static bool ValidText(string? text)
    {
        if (string.IsNullOrWhiteSpace(text) || text.Length > 500) return false;
        var compact = Regex.Replace(text.Trim(), @"\s+", " ");
        if (Regex.IsMatch(compact,
            "page (?:was )?not found|requested page|music discovery|" +
            "\u8981\u6c42\u3055\u308c\u305f\u30da\u30fc\u30b8\u306f\u898b\u3064\u304b\u308a\u307e\u305b\u3093|" +
            "\u30e6\u30fc\u30b6\u30fc\u304c\u4ecaShazam\u3067\u898b\u3064\u3051\u3066\u3044\u308b\u66f2|" +
            "\u97f3\u697d\u767a\u898b\u3001\u30c1\u30e3\u30fc\u30c8", RegexOptions.IgnoreCase)) return false;
        return !Regex.IsMatch(compact,
            @"\A(?:(?:shazam\s*)?(?:フッター|footer|ヘッダー|header|ナビゲーション|navigation)|概要|overview|歌詞|lyrics|ビデオ|videos?|ミュージックビデオ|music video|関連|related|クレジット|credits|トップソング|top songs|アルバム|albums|おすすめ|featured)\z",
            RegexOptions.IgnoreCase);
    }

    private static string ReadString(JsonElement root, string name) =>
        root.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? "" : "";

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _lifetime.Cancel();
            _webView.Dispose();
            // ReadCommandsAsync may still be observing this token during close.
        }
        base.Dispose(disposing);
    }
}
