package app.ritm.food

import app.ritm.data.json
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Продукт из открытой базы Open Food Facts (на 100 г). */
data class OffProduct(val name: String, val brand: String?, val kcal: Double, val protein: Double?, val fat: Double?, val carbs: Double?, val barcode: String?) {
    val title: String get() = if (brand.isNullOrBlank()) name else "$name ($brand)"
}

/**
 * Open Food Facts — открытая база продуктов со штрихкодами, есть российские товары.
 * Нужен интернет; без него просто ничего не находится.
 */
object OpenFoodFacts {
    private const val UA = "Ritm/0.1 (personal food log)"
    private const val FIELDS = "code,product_name,product_name_ru,brands,nutriments"

    suspend fun search(query: String): List<OffProduct> = withContext(Dispatchers.IO) {
        val q = URLEncoder.encode(query.trim(), "UTF-8")
        val body = get("https://world.openfoodfacts.org/cgi/search.pl?search_terms=$q&search_simple=1&action=process&json=1&page_size=20&lc=ru&fields=$FIELDS")
            ?: return@withContext emptyList()
        val products = runCatching { json.parseToJsonElement(body).jsonObject["products"]?.jsonArray }.getOrNull() ?: return@withContext emptyList()
        products.mapNotNull { runCatching { parse(it.jsonObject) }.getOrNull() }.distinctBy { it.title.lowercase() }
    }

    suspend fun byBarcode(code: String): OffProduct? = withContext(Dispatchers.IO) {
        val body = get("https://world.openfoodfacts.org/api/v2/product/${URLEncoder.encode(code, "UTF-8")}.json?fields=$FIELDS") ?: return@withContext null
        val root = runCatching { json.parseToJsonElement(body).jsonObject }.getOrNull() ?: return@withContext null
        val p = root["product"]?.jsonObject ?: return@withContext null
        runCatching { parse(p, code) }.getOrNull()
    }

    private fun parse(p: JsonObject, code: String? = null): OffProduct? {
        fun str(k: String) = p[k]?.jsonPrimitive?.contentOrNull?.trim()?.takeIf { it.isNotEmpty() }
        val n = p["nutriments"]?.jsonObject ?: return null
        fun num(k: String) = n[k]?.jsonPrimitive?.let { it.doubleOrNull ?: it.contentOrNull?.replace(',', '.')?.toDoubleOrNull() }
        val kcal = num("energy-kcal_100g") ?: num("energy_100g")?.let { it / 4.184 } ?: return null
        val name = str("product_name_ru") ?: str("product_name") ?: return null
        return OffProduct(
            name = name.replaceFirstChar { it.uppercase() },
            brand = str("brands")?.split(',')?.firstOrNull()?.trim(),
            kcal = kcal, protein = num("proteins_100g"), fat = num("fat_100g"), carbs = num("carbohydrates_100g"),
            barcode = code ?: str("code"),
        )
    }

    private fun get(url: String): String? {
        val c = URL(url).openConnection() as HttpURLConnection
        return try {
            c.connectTimeout = 8_000; c.readTimeout = 12_000
            c.setRequestProperty("User-Agent", UA)
            if (c.responseCode !in 200..299) null else c.inputStream.bufferedReader().use { it.readText() }
        } catch (_: Exception) {
            null
        } finally {
            c.disconnect()
        }
    }
}
