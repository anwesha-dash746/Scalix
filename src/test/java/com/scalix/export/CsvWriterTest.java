package com.scalix.export;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class CsvWriterTest {

    @Test
    void escapesAndFormats(@TempDir Path dir) throws IOException {
        final Path f = dir.resolve("sub/x.csv");
        try (CsvWriter w = new CsvWriter(f, List.of("a", "b", "c", "d"))) {
            w.writeRow("plain", "has,comma", "has \"quote\"", 1.50);
            w.writeRow(Double.NaN, 3, true, null);
        }
        final List<String> lines = Files.readAllLines(f);
        assertEquals("a,b,c,d", lines.get(0));
        assertEquals("plain,\"has,comma\",\"has \"\"quote\"\"\",1.5", lines.get(1));
        assertEquals(",3,true,", lines.get(2));
    }

    @Test
    void numbersAreLocaleIndependent() {
        assertEquals("0.123457", CsvWriter.format(0.1234567));
        assertEquals("2", CsvWriter.format(2.0));
        assertEquals("-0.5", CsvWriter.format(-0.5));
    }

    @Test
    void wrongColumnCountRejected(@TempDir Path dir) throws IOException {
        try (CsvWriter w = new CsvWriter(dir.resolve("y.csv"), List.of("a", "b"))) {
            assertThrows(IllegalArgumentException.class, () -> w.writeRow("only one"));
        }
    }
}
