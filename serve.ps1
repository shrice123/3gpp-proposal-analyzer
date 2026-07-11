param($RootDir, $Port)

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
$listener.Start()

$mimeTypes = @{
    '.html' = 'text/html; charset=utf-8'
    '.js'   = 'application/javascript'
    '.css'  = 'text/css'
    '.svg'  = 'image/svg+xml'
    '.woff2'= 'font/woff2'
    '.json' = 'application/json'
    '.png'  = 'image/png'
    '.ico'  = 'image/x-icon'
}

while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    $relPath = $ctx.Request.Url.LocalPath.TrimStart('/')
    if ($relPath -eq '') { $relPath = 'index.html' }
    $fullPath = Join-Path $RootDir $relPath

    if ((Test-Path $fullPath -PathType Leaf) -and $fullPath.StartsWith($RootDir, [StringComparison]::OrdinalIgnoreCase)) {
        $ext = [IO.Path]::GetExtension($fullPath)
        $ctx.Response.ContentType = if ($mimeTypes.ContainsKey($ext)) { $mimeTypes[$ext] } else { 'application/octet-stream' }
        $bytes = [IO.File]::ReadAllBytes($fullPath)
        $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    } else {
        $ctx.Response.StatusCode = 404
    }
    $ctx.Response.Close()
}
