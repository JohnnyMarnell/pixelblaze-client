from pixelblaze import *
import base64
import json

# 1. Connect to your Pixelblaze
ip_address = "192.168.1.156"  # Replace with your IP
pb = Pixelblaze(ip_address)

# 2. Define your pattern source code (e.g., a simple blinking red pattern)
pattern_source = """
export function render(index) {
  h = 0
  s = 1
  v = wave(time(0.1))
  hsv(h, s, v)
}
"""

pattern_name = "My Python Pattern"

try:
    print("Compiling pattern...")
    # The library fetches the compiler from the PB and compiles your code
    # This returns a binary blob (bytecode)
    bytecode = pb.compilePattern(pattern_source)
    
    if bytecode:
        print("Compilation successful. Uploading...")
        
        # 3. Create a pattern object or save directly
        # The library often uses a 'PBP' (PixelBlaze Pattern) helper or allows passing bytecode
        # Check if your version supports savePattern with bytecode or requires a PBP object.
        # Common method signature (may vary slightly by version):
        pb.savePattern(bytecode, pattern_name)
        
        # Alternatively, for some versions/contexts you might need to specify it's a new pattern:
        # pb.saveNewPattern(bytecode, pattern_name)
        
        print(f"Pattern '{pattern_name}' saved successfully!")
    else:
        print("Compilation failed.")

except Exception as e:
    print(f"An error occurred: {e}")