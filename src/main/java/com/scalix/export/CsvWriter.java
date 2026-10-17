package com.scalix.export;

import java.io.BufferedWriter;
import java.io.Closeable;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Locale;

/**
 * Minimal RFC 4180 CSV writer. Numbers use '.' as decimal separator regardless of locale; NaN is
 * written as an empty field so tools such as pandas read it as missing.
 */
public final class CsvWriter implements Closeable {

    private final BufferedWriter out;
    private final int columns;

    public CsvWriter(Path file, List<String> header) throws IOException {
        Files.createDirectories(file.toAbsolutePath().getParent());
        this.out = Files.newBufferedWriter(file, StandardCharsets.UTF_8);
        this.columns = header.size();
        writeRow(header.toArray());
    }

    public void writeRow(Object... values) throws IOException {
        if (values.length != columns) {
            throw new IllegalArgumentException("Expected " + columns + " columns, got " + values.length);
        }
        final StringBuilder sb = new StringBuilder();
        for (int i = 0; i < values.length; i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(escape(format(values[i])));
        }
        out.write(sb.toString());
        out.write("\r\n");
    }

    static String format(Object v) {
        if (v == null) {
            return "";
        }
        if (v instanceof Double d) {
            if (d.isNaN()) {
                return "";
            }
            if (d.isInfinite()) {
                return d > 0 ? "Infinity" : "-Infinity";
            }
            return String.format(Locale.ROOT, "%.6f", d).replaceAll("0+$", "").replaceAll("\\.$", "");
        }
        return v.toString();
    }

    static String escape(String s) {
        if (s.contains(",") || s.contains("\"") || s.contains("\n") || s.contains("\r")) {
            return '"' + s.replace("\"", "\"\"") + '"';
        }
        return s;
    }

    @Override
    public void close() throws IOException {
        out.close();
    }
}
