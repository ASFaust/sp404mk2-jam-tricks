// Creates the functions Ghidra's auto-analysis misses. Adapted from Tallfree's MkMissing/MkFns
// (resources/repos/tallfree/ghidra, MIT licence).
//
// Most C++ methods are only reached through vtables and pointer tables, so analysis leaves
// them as orphan instructions or undisassembled bytes. This creates a function at:
//   1. every call target that has none yet,
//   2. every word in the SDRAM code/data blocks that looks like a Thumb pointer (bit 0 set)
//      into code,
//   3. a list of tail-call functions (no push) Tallfree found by hand.
//@category SP404

import java.util.Set;
import java.util.TreeSet;

import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

public class FixCoverage extends GhidraScript {
	private static final long[] TAIL_CALL_FUNCTIONS = {
		0x80124f38L, 0x800ff060L, 0x80138180L, 0x801386d0L, 0x80141c98L, 0x80172188L,
		0x80101778L, 0x80111468L, 0x80114188L, 0x8012b9b8L, 0x80168ac0L, 0x80003342L,
		0x8000327aL, 0x80003380L, 0x80008ec8L, 0x80009490L, 0x80034d4cL, 0x80074500L,
		0x800fea30L, 0x800ffcb8L, 0x801016e0L, 0x80111458L,
	};

	private long orphans() {
		long n = 0;
		InstructionIterator it = currentProgram.getListing().getInstructions(true);
		while (it.hasNext()) {
			Instruction i = it.next();
			if (getFunctionContaining(i.getAddress()) == null) {
				n++;
			}
		}
		return n;
	}

	private boolean isCode(Address a) {
		MemoryBlock b = currentProgram.getMemory().getBlock(a);
		return b != null && b.isExecute() && b.isInitialized();
	}

	private boolean make(Address a, boolean disassembleFirst) {
		if (getFunctionAt(a) != null) {
			return false;
		}
		if (getInstructionAt(a) == null) {
			if (!disassembleFirst || getDataAt(a) != null && getDataAt(a).isDefined()) {
				return false;
			}
			disassemble(a);
			if (getInstructionAt(a) == null) {
				return false;
			}
		}
		return new CreateFunctionCmd(a).applyTo(currentProgram, monitor);
	}

	@Override
	protected void run() throws Exception {
		println("functions before: " + currentProgram.getFunctionManager().getFunctionCount()
			+ ", orphan instructions: " + orphans());

		Set<Address> calls = new TreeSet<>();
		ReferenceIterator ri = currentProgram.getReferenceManager().getReferenceIterator(toAddr(0));
		while (ri.hasNext()) {
			Reference r = ri.next();
			if (r.getReferenceType().isCall() && isCode(r.getToAddress())) {
				calls.add(r.getToAddress());
			}
		}
		int c = 0;
		for (Address a : calls) {
			if (make(a, false)) {
				c++;
			}
		}
		println("created from calls: " + c);

		// Thumb code pointers stored in the code and initialized-data blocks.
		Memory mem = currentProgram.getMemory();
		Set<Address> ptrs = new TreeSet<>();
		for (MemoryBlock b : mem.getBlocks()) {
			if (!b.isInitialized() || !b.getStart().isMemoryAddress()) {
				continue;
			}
			Address end = b.getEnd();
			for (Address a = b.getStart(); a.add(3).compareTo(end) <= 0; a = a.add(4)) {
				long v = mem.getInt(a) & 0xffffffffL;
				if ((v & 1) == 0) {
					continue;
				}
				Address t = toAddr(v & ~1L);
				if (isCode(t)) {
					ptrs.add(t);
				}
			}
		}
		int p = 0;
		for (Address a : ptrs) {
			if (make(a, false)) {
				p++;
			}
		}
		println("created from pointers: " + p + " (of " + ptrs.size() + " candidates)");

		int t = 0;
		for (long v : TAIL_CALL_FUNCTIONS) {
			if (make(toAddr(v), true)) {
				t++;
			}
		}
		println("created tail-call functions: " + t);

		println("functions after: " + currentProgram.getFunctionManager().getFunctionCount()
			+ ", orphan instructions: " + orphans());
	}
}
