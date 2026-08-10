param(
    [Parameter(Mandatory = $true)]
    [string]$InputFile
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapTransform, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]

function Await-WinRT {
    param($Operation, [Type]$ResultType)
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and
            $_.GetParameters().Count -eq 1
        } |
        Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}

$resolved = (Resolve-Path -LiteralPath $InputFile).Path
$file = Await-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($resolved)) ([Windows.Storage.StorageFile])
$stream = Await-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
try {
    $decoder = Await-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])

    # OcrEngine.RecognizeAsync rejects any bitmap wider or taller than
    # MaxImageDimension (10000px on this engine). Panoramas and large scans
    # exceed that, so decode at a scaled-down size instead of failing --
    # OCR on a downscaled image still beats no OCR at all.
    $maxDimension = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    $width = $decoder.PixelWidth
    $height = $decoder.PixelHeight
    if ($width -gt $maxDimension -or $height -gt $maxDimension) {
        $scale = $maxDimension / [Math]::Max($width, $height)
        $transform = [Windows.Graphics.Imaging.BitmapTransform]::new()
        $transform.ScaledWidth = [Math]::Max(1, [int][Math]::Floor($width * $scale))
        $transform.ScaledHeight = [Math]::Max(1, [int][Math]::Floor($height * $scale))
        $bitmap = Await-WinRT (
            $decoder.GetSoftwareBitmapAsync(
                $decoder.BitmapPixelFormat, $decoder.BitmapAlphaMode, $transform,
                [Windows.Graphics.Imaging.ExifOrientationMode]::RespectExifOrientation,
                [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage
            )
        ) ([Windows.Graphics.Imaging.SoftwareBitmap])
    }
    else {
        $bitmap = Await-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    }
    try {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
        $result = Await-WinRT ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $result.Text
    }
    finally {
        if ($bitmap) { $bitmap.Dispose() }
    }
}
finally {
    if ($stream) { $stream.Dispose() }
}
