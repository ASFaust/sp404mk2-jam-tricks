// Prints a summary of the analysed program: functions, strings, memory blocks.
// Headless: ghidra.analyzeHeadless ghidra sp404mk2 -process app1.elf -noanalysis -readOnly \
//               -scriptPath app1/tools/ghidra_scripts -postScript ProjectStats.java
//@category SP404

import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.DataIterator;
import ghidra.program.model.mem.MemoryBlock;

public class ProjectStats extends GhidraScript {
	@Override
	protected void run() throws Exception {
		println("language: " + currentProgram.getLanguageID());
		for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
			println(String.format("block %-28s %s - %s", b.getName(), b.getStart(), b.getEnd()));
		}
		println("functions: " + currentProgram.getFunctionManager().getFunctionCount());
		int strings = 0;
		DataIterator it = currentProgram.getListing().getDefinedData(true);
		while (it.hasNext()) {
			Data d = it.next();
			if (d.hasStringValue()) {
				strings++;
			}
		}
		println("defined strings: " + strings);
	}
}
