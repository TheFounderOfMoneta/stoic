package app.ritm.food

import android.content.Context
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/** Сканер штрихкодов Google Play: свой экран камеры, разрешение на камеру приложению не нужно. */
object BarcodeScanner {
    /** @return код или null, если человек закрыл сканер. */
    suspend fun scan(context: Context): String? = suspendCancellableCoroutine { cont ->
        val options = GmsBarcodeScannerOptions.Builder()
            .setBarcodeFormats(Barcode.FORMAT_EAN_13, Barcode.FORMAT_EAN_8, Barcode.FORMAT_UPC_A, Barcode.FORMAT_UPC_E)
            .enableAutoZoom()
            .build()
        GmsBarcodeScanning.getClient(context, options).startScan()
            .addOnSuccessListener { if (cont.isActive) cont.resume(it.rawValue) }
            .addOnCanceledListener { if (cont.isActive) cont.resume(null) }
            .addOnFailureListener { if (cont.isActive) cont.resume(null) }
    }
}
