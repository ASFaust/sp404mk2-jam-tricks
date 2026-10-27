// Lists every reference to the given addresses, with the function each one is in.
// Read-only; run through app1/tools/xrefs.sh:
//   app1/tools/xrefs.sh 80146260
//@category SP404

import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

public class Xrefs extends GhidraScript {
	@Override
	protected void run() throws Exception {
		for (String arg : getScriptArgs()) {
			println("@@@ references to " + arg);
			for (Reference r : getReferencesTo(toAddr(Long.parseLong(arg, 16) & ~1L))) {
				Function f = getFunctionContaining(r.getFromAddress());
				println(String.format("  %s %-14s in %s", r.getFromAddress(), r.getReferenceType(),
						f == null ? "<no function>" : f.getName() + " @ " + f.getEntryPoint()));
			}
		}
	}
}
