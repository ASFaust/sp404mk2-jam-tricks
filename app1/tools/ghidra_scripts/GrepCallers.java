// Decompiles every function that calls TARGET and prints the lines matching REGEX.
// Read-only; run through app1/tools/grepcallers.sh:
//   app1/tools/grepcallers.sh 800DDA38 '0xbf|0xc0'       (who reads parameters 0xBF / 0xC0)
// Several targets: separate them with commas (800DDA38,800DA010).
//@category SP404

import java.util.LinkedHashSet;
import java.util.Set;
import java.util.regex.Pattern;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

public class GrepCallers extends GhidraScript {
	@Override
	protected void run() throws Exception {
		String[] args = getScriptArgs();
		Pattern re = Pattern.compile(args[1]);
		Set<Function> callers = new LinkedHashSet<>();
		for (String t : args[0].split(",")) {
			for (Reference r : getReferencesTo(toAddr(Long.parseLong(t, 16) & ~1L))) {
				Function f = getFunctionContaining(r.getFromAddress());
				if (f != null) {
					callers.add(f);
				}
			}
		}
		println("@@@ " + callers.size() + " callers of " + args[0] + ", grep /" + args[1] + "/");
		DecompInterface di = new DecompInterface();
		di.openProgram(currentProgram);
		for (Function f : callers) {
			DecompileResults r = di.decompileFunction(f, 60, monitor);
			if (!r.decompileCompleted()) {
				continue;
			}
			for (String line : r.getDecompiledFunction().getC().split("\n")) {
				if (re.matcher(line).find()) {
					println(f.getName() + " @ " + f.getEntryPoint() + ":  " + line.trim());
				}
			}
		}
	}
}
