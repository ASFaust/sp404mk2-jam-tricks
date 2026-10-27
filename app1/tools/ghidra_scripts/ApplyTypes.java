// Applies structs and function signatures from a types file (default app1/tools/types.txt).
// Safe to re-run: structs are replaced by the file's version, signatures re-applied.
// Headless: ghidra.analyzeHeadless ghidra sp404mk2 -process app1.elf -noanalysis \
//               -scriptPath app1/tools/ghidra_scripts -postScript ApplyTypes.java app1/tools/types.txt
//
// File format (# starts a comment; a comment on a field line becomes the field's comment):
//   struct NAME SIZE            start a struct of SIZE bytes (hex with 0x, or decimal)
//     OFFSET TYPE FIELD         one field; TYPE is a C type: int, uint, short, ushort, byte,
//                               bool, float, double, void*, char*, another struct, NAME* ...
//   end
//   sig ADDRESS C-SIGNATURE     e.g. sig 80146598 void EfxEdit_Update(EfxEdit *this)
//   data ADDRESS TYPE           give a global a type, e.g. data 801AA0A0 int[16]
// Lines are applied in file order, so define a struct before using it. TYPE may end in
// [N] for an array.
//@category SP404

import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.List;

import ghidra.app.cmd.function.ApplyFunctionSignatureCmd;
import ghidra.app.script.GhidraScript;
import ghidra.app.util.cparser.C.CParserUtils;
import ghidra.program.model.address.Address;
import ghidra.program.model.data.*;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.SourceType;

public class ApplyTypes extends GhidraScript {
	static final CategoryPath CATEGORY = new CategoryPath("/sp404");

	DataTypeManager dtm;

	@Override
	protected void run() throws Exception {
		String path = getScriptArgs().length > 0 ? getScriptArgs()[0]
				: askFile("Types file", "Apply").getAbsolutePath();
		dtm = currentProgram.getDataTypeManager();
		List<String> lines = Files.readAllLines(Paths.get(path));
		int structs = 0, sigs = 0, datas = 0, failed = 0;
		StructureDataType cur = null;
		for (String raw : lines) {
			String comment = null;
			String line = raw;
			int hash = line.indexOf('#');
			if (hash >= 0) {
				comment = line.substring(hash + 1).trim();
				line = line.substring(0, hash);
			}
			line = line.trim();
			if (line.isEmpty()) {
				continue;
			}
			String[] f = line.split("\\s+");
			try {
				if (f[0].equals("struct")) {
					cur = new StructureDataType(CATEGORY, f[1], (int) num(f[2]), dtm);
					if (comment != null) {
						cur.setDescription(comment);
					}
				} else if (f[0].equals("end")) {
					dtm.addDataType(cur, DataTypeConflictHandler.REPLACE_HANDLER);
					cur = null;
					structs++;
				} else if (f[0].equals("sig")) {
					Address a = toAddr(Long.parseLong(f[1], 16));
					String sig = line.substring(line.indexOf(f[1]) + f[1].length()).trim();
					if (getFunctionAt(a) == null) {
						createFunction(a, null);
					}
					FunctionDefinitionDataType def = CParserUtils.parseSignature(
							(ghidra.app.services.DataTypeManagerService) null, currentProgram, sig);
					if (def == null || !new ApplyFunctionSignatureCmd(a, def, SourceType.USER_DEFINED)
							.applyTo(currentProgram)) {
						throw new Exception("could not apply");
					}
					Function fn = getFunctionAt(a);
					fn.setCallingConvention(
							currentProgram.getCompilerSpec().getDefaultCallingConvention().getName());
					if (comment != null) {
						fn.setComment(comment);
					}
					sigs++;
				} else if (f[0].equals("data")) {
					Address a = toAddr(Long.parseLong(f[1], 16));
					DataType dt = type(line.substring(line.indexOf(f[1]) + f[1].length()).trim());
					clearListing(a, a.add(dt.getLength() - 1));
					createData(a, dt);
					datas++;
				} else if (cur != null) {
					// OFFSET TYPE... FIELD: the type may contain spaces ("unsigned int")
					int off = (int) num(f[0]);
					String field = f[f.length - 1];
					String type = line.substring(line.indexOf(f[1]), line.lastIndexOf(field)).trim();
					DataType dt = type(type);
					cur.replaceAtOffset(off, dt, dt.getLength(), field, comment);
				} else {
					throw new Exception("unknown line");
				}
			} catch (Exception e) {
				println("FAILED: " + raw + "  (" + e.getMessage() + ")");
				failed++;
			}
		}
		println("structs " + structs + ", signatures " + sigs + ", data " + datas + ", failed " + failed);
	}

	long num(String s) {
		return s.startsWith("0x") ? Long.parseLong(s.substring(2), 16) : Long.parseLong(s);
	}

	DataType type(String t) throws Exception {
		t = t.replace(" ", "");
		if (t.endsWith("]")) {
			int open = t.lastIndexOf('[');
			DataType elem = type(t.substring(0, open));
			int n = (int) num(t.substring(open + 1, t.length() - 1));
			return new ArrayDataType(elem, n, elem.getLength(), dtm);
		}
		if (t.endsWith("*")) {
			return new PointerDataType(type(t.substring(0, t.length() - 1)), dtm);
		}
		switch (t) {
		case "int": return IntegerDataType.dataType;
		case "uint": return UnsignedIntegerDataType.dataType;
		case "short": return ShortDataType.dataType;
		case "ushort": return UnsignedShortDataType.dataType;
		case "byte": return ByteDataType.dataType;
		case "char": return CharDataType.dataType;
		case "bool": return BooleanDataType.dataType;
		case "float": return FloatDataType.dataType;
		case "double": return DoubleDataType.dataType;
		case "void": return VoidDataType.dataType;
		}
		DataType dt = dtm.getDataType(CATEGORY, t);
		if (dt == null) {
			throw new Exception("unknown type " + t);
		}
		return dt;
	}
}
