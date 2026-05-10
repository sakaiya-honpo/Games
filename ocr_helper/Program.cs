using System;
using System.IO;
using System.Threading.Tasks;
using Windows.Media.Ocr;
using Windows.Graphics.Imaging;
using Windows.Storage;
using Windows.Globalization;

class Program
{
    static int Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.Error.WriteLine("Usage: ocr_helper <image_path> <lang>");
            return 1;
        }
        // Task.Run でスレッドプール上で実行（WinRT async に適した MTA コンテキスト）
        return Task.Run(() => RunAsync(args[0], args[1])).GetAwaiter().GetResult();
    }

    static async Task<int> RunAsync(string imagePath, string lang)
    {
        try
        {
            Console.OutputEncoding = System.Text.Encoding.UTF8;
            var fullPath = Path.GetFullPath(imagePath);
            var file    = await StorageFile.GetFileFromPathAsync(fullPath);
            var stream  = await file.OpenReadAsync();
            var decoder = await BitmapDecoder.CreateAsync(stream);
            var bitmap  = await decoder.GetSoftwareBitmapAsync();
            var engine  = OcrEngine.TryCreateFromLanguage(new Language(lang));
            if (engine == null)
            {
                Console.Error.WriteLine("OCR engine unavailable for: " + lang);
                return 1;
            }
            var result = await engine.RecognizeAsync(bitmap);
            Console.WriteLine(result.Text);
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex.ToString());
            return 1;
        }
    }
}
