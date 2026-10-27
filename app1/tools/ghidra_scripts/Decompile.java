// Prints the decompiled C of the functions containing the given addresses.
// Read-only; run headless and pipe through app1/tools/decomp.sh, which strips Ghidra's log noise:
//   app1/tools/decomp.sh 80147f30 80146260
//@category SP404

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;

public class Decompile extends GhidraScript {
	@Override
	protected void run() throws Exception {
		DecompInterface di = new DecompInterface();
		di.openProgram(currentProgram);
		for (String arg : getScriptArgs()) {
			Function f = getFunctionContaining(toAddr(Long.parseLong(arg, 16) & ~1L));
			if (f == null) {
				println("@@@ " + arg + ": no function here");
				continue;
			}
			DecompileResults r = di.decompileFunction(f, 120, monitor);
			println("@@@ " + arg + ": " + f.getName() + " @ " + f.getEntryPoint() + "\n"
					+ (r.decompileCompleted() ? r.getDecompiledFunction().getC() : "decompile failed"));
		}
	}
}
