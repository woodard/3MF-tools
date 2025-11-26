# 3MF-tools

The primary purpose of this tool was to take Assemblies of multiboard
and print out the BOM so that other people could reproduce the
assembly without violating the multiboard license.

I plan to add additional scripts as I find that I need them.

## 3mf_bom_parser.py 

take a 3mf file and print out a bill of materials
based upon the names of the STL files and print out a Bill of
Materials.

## import_bom.py

** Untested **

Take a BOM created by `3mf_bom_parser.py --multiboard` script and
process it, download all the files, and use prusa-slicer to load them
in and create a 3mf file. 

## Intended usage

Once this is tested and working, the idea would be someone with a
multiboard project could make a BOM using the first tool. Then someone
else could take that BOM and import it making a 3mf file. Hopefully
this file can then be read into Bambu Studio or some other slicer and
then the user can split it up into plates and arrange the objects on
the plates, select the filiments and then print the assembly.
