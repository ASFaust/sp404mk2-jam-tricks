// Applies names from a symbols file (default app1/tools/symbols_tallfree.txt).
// Line format: ADDRESS KIND NAME [# comment], KIND f = function, d = data label.
// Existing user-chosen names are kept: only default names (FUN_..., DAT_...) are replaced.
// Headless: ghidra.analyzeHeadless ghidra sp404mk2 -process app1.elf -noanalysis \
//               -scriptPath app1/tools/ghidra_scripts -postScript ApplySymbols.java <file>
//@category SP404

import java.nio.file.Files;
import java.nio.file.Paths;

import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.CodeUnit;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.symbol.Symbol;

public class ApplySymbols extends GhidraScript {
	@Override
	protected void run() throws Exception {
		String path = getScriptArgs().length > 0 ? getScriptArgs()[0]
				: askFile("Symbols file", "Apply").getAbsolutePath();
		int named = 0, kept = 0, failed = 0;
		for (String line : Files.readAllLines(Paths.get(path))) {
			String comment = null;
			int hash = line.indexOf('#');
			if (hash >= 0) {
				comment = line.substring(hash + 1).trim();
				line = line.substring(0, hash);
			}
			String[] f = line.trim().split("\\s+");
			if (f.length < 3) {
				continue;
			}
			Address a = toAddr(Long.parseLong(f[0], 16));
			String name = f[2];
			Symbol existing = getSymbolAt(a);
			if (f[1].equals("f")) {
				Function fn = getFunctionAt(a);
				if (fn == null) {
					disassemble(a);
					new CreateFunctionCmd(a).applyTo(currentProgram, monitor);
					fn = getFunctionAt(a);
				}
				if (fn == null) {
					println("could not create function at " + a + " for " + name);
					failed++;
					continue;
				}
				if (fn.getSymbol().getSource() == SourceType.USER_DEFINED
						&& !fn.getName().equals(name)) {
					kept++;
					continue;
				}
				fn.setName(name, SourceType.IMPORTED);
			}
			else {
				if (existing != null && existing.getSource() == SourceType.USER_DEFINED
						&& !existing.getName().equals(name)) {
					kept++;
					continue;
				}
				createLabel(a, name, true, SourceType.IMPORTED);
			}
			if (comment != null && !comment.isEmpty()) {
				setPlateComment(a, comment);
			}
			named++;
		}
		println("named " + named + ", kept user names " + kept + ", failed " + failed);
	}
}
