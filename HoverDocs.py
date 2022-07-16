import sublime
import sublime_plugin
import os
import html
import re

class HoverDocsCommand(sublime_plugin.TextCommand):
	def run(self, edit, mode="open", args=None):
		if mode == "append":
			self.view.insert(edit, self.view.size(), args['characters'])

class HoverDocsListener(sublime_plugin.EventListener):
	def __init__(self, *vargs, **kwargs):
		super().__init__(*vargs, **kwargs)
		self.hover_line = -1
		self.dclick_annotations = []
		self.pinned_annotations = []

	def setting(self, setting):
		return sublime.load_settings("HoverDocs.sublime-settings")[setting]

	def find_symbol_definition(self, view, sym_name):
		""" Searches the sublime index of symbols for the closest matching definition of sym_name.

		The "closest match" is according to the following rules, in order of presedence:
		1. the view's file
		2. open files
		3. files with the same extension as the given view
		4. "path distance" between the file for the given view and the definition file

		Args:
		    view: the active view
		    sym_name: the name of the symbol to look for
	    
	    Returns:
			The SymbolLocation, or None if not found.
			https://www.sublimetext.com/docs/api_reference.html#sublime.SymbolLocation
		"""
		win = sublime.active_window()
		sym_locs = win.symbol_locations(sym=sym_name)
		if len(sym_locs) > 0:
			_subl_definition_type = 1
			sym_loc = None
			fn = view.file_name()
			a, ext = os.path.splitext(fn)

			# find the best fitting definition
			defs = list(filter(lambda sl: sl.type == _subl_definition_type, sym_locs))
			if len(defs) == 0:
				return None

			# presedence (1)
			defs_samefile = list(filter(lambda sl: sl.path == fn, defs))
			if len(defs_samefile) > 0:
				defs = defs_samefile

			# For sorting by distances between two file paths. The distance for "foobar.txt" between the following example paths is 5:
			# /this/is/an/example/path/foobar.txt
			# /this/is/another/path/foobar.txt
			def get_dirs(path):
				ret = []
				path, base = os.path.split(path)
				while base != "":
					path, base = os.path.split(path)
					ret.append(base)
				ret.reverse()
				return ret
			fn_dirs = get_dirs(fn)
			def get_path_dist(sym_loc):
				sym_dirs = get_dirs(sym_loc.path)
				for i in range(len(fn_dirs)):
					if sym_dirs[i] != fn_dirs[i]:
						break
				path_dist = len(fn_dirs)-i + len(sym_dirs)-i
				return path_dist

			# Build a collection of filters to find the most appropriate result.
			# We filter down until there aren't any sym_locs left, or we've run out of filters.
			# The order of the filters matters.
			filter_open    = lambda locs: list(filter(lambda sl: win.find_open_file(sl.path) != None, locs)) # presedence (2)
			filter_extents = lambda locs: list(filter(lambda sl: sl.path.endswith(ext), locs))               # presedence (3)
			sort_path_dist = lambda locs: list(sorted(locs, key=get_path_dist))                              # presedence (4)
			filters = [filter_open, filter_extents, sort_path_dist]

			# Find the best fitting sym_loc
			sym_loc = defs[0]
			for sym_filter in filters:
				defs = sym_filter(defs)
				if len(defs) == 0:
					break
				sym_loc = defs[0]

			return sym_loc
		else:
			# print("No matching symbol at point")
			return None

	def on_text_command(self, view, command_name, args):
		if command_name == "hover_docs":
			if (args == None) or (not "mode" in args) or (args["mode"] == "open"):
				regs = filter(lambda r: r.size() == 0, view.sel())
				docregs, docstrs, sym_locs = [], [], []
				for reg in regs:
					docstr, sym_loc = self.prep_doc(view, reg.a)
					if docstr != None:
						docregs.append(sublime.Region(reg.a, reg.a))
						docstrs.append(docstr)
						sym_locs.append(sym_loc)
				if len(docregs) > 0:
					self.add_docs(view, docregs, docstrs, sym_locs)

	def on_hover(self, view, point, hover_zone):
		docstr, sym_loc = self.prep_doc(view, point)
		hover_line = view.rowcol(point)[0]

		if docstr == None:
			if hover_line != self.hover_line:
				if self.setting("hover_auto_hide"):
					view.erase_regions("hd_hover")
		else:
			self.add_docs(view, [sublime.Region(point, point)], [docstr], [sym_loc])
		self.hover_line = hover_line

	def prep_doc(self, view, point, force_docstring=None, force_interface=None, force_hyperlink=None):
		""" Finds the definition for the reference symbol at the given point (if any)
		and builds out the documentation string.

		Args:
		    view: the view to grab the symbol from
		    point: the location in the view to grab the symbol from
		    force*: True or False to force the documentation string, None to obey the settings file
		Returns:
		    docstr: The documentation string, or None if not applicable
		    sym_loc: The SymbolLocation, or None if not applicable
		"""
		point_reg = sublime.Region(point, point)
		scope = view.extract_tokens_with_scopes(point_reg)
		regs = []
		annotations = []
		sym_loc = None

		# some basic qualifications
		if point >= view.size():
			return None, None
		if len(scope) == 0:
			return None, None
		scope_reg = scope[0][0]
		scope_names = scope[0][1]
		if "comment" in scope_names:
			return None, None

		# check if this symbol _is_ the definition
		sym_regs = view.symbol_regions()
		found = False
		for sym_reg in sym_regs:
			if sym_reg.region.contains(point):
				if sym_reg.type == 1: # 1 == Definition
					return None, None

		# symbol at scope_reg must be a reference, try to find the definition
		# try to find a definition with the same name in the index
		sym_name = view.substr(scope_reg)
		sym_loc = self.find_symbol_definition(view, sym_name)
		if sym_loc == None:
			return None, None
		fn = os.path.basename(sym_loc.path)

		# get the def_str and comment_str, with syntax applied via minihtml
		v2, def_reg, comment_reg = self.find_def_and_comment(sym_loc, sym_name)
		def_scopes, comment_scopes = self.get_scope_spans(v2, def_reg), self.get_scope_spans(v2, comment_reg)
		def_str, comment_str = v2.substr(def_reg), v2.substr(comment_reg)
		def_str, comment_str = self.apply_syntax(v2, def_str, def_scopes), self.apply_syntax(v2, comment_str, comment_scopes)
		
		# build the docstr
		docstr = ""
		if (self.setting("display_docstring") or force_docstring == True) and (force_docstring != False):
			if len(def_str) > 0:
				docstr += def_str
		if (self.setting("display_interface") or force_interface == True) and (force_interface != False):
			if len(comment_str) > 0:
				docstr += ("" if len(docstr) == 0 else "<br>") + comment_str
		if (self.setting("display_file_hyperlink") or force_hyperlink == True) and (force_hyperlink != False):
			docstr += ("" if len(docstr) == 0 else "<br>") + f"<a href='!href!'>{fn}:{sym_loc.row+1}</a>"

		return docstr, sym_loc

	def apply_syntax(self, view, strval, scope_spans):
		""" Inserts minihtml into the given string to match the syntax of the given spans.

		Args:
		    view: The view that the given string is from.
		    strval: The string to insert the syntax into.
		    scope_spans: A list of [idx, len, scope_name] used to get the syntax.
		Returns:
		    str_wsyntax: The string with html markup inserted.
		"""
		ret = ""

		# get the default foreground color
		default_style = view.style_for_scope('')

		for scope_span in scope_spans:
			# split the string into scope span pieces
			idx, length, scope_names = scope_span
			strpart = strval[idx:idx+length]

			# html encode the string
			strpart = html.escape(strpart)
			strpart = re.sub(r" ( +)", lambda m: "&nbsp;"*len(m.group(0)), strpart)
			strpart = strpart.replace("\n","<br>")

			# get the style for this scope
			style = view.style_for_scope(scope_names[0])
			for scope_name in scope_names[1:]:
				tmp_style = view.style_for_scope(scope_name)
				if style['foreground'] == default_style['foreground']:
					style = tmp_style
				if tmp_style['foreground'] != default_style['foreground']:
					style = tmp_style

			# apply the syntax for this piece
			style_str = f"<div style='display:inline; color:{style['foreground']};"
			if "background" in style:
				style_str += " background-color:{style['background']};"
			if "bold" in style and style["bold"]:
				style_str += " font-weight:bold;"
			if "italic" in style and style["italic"]:
				style_str += " font-style:italic;"
			if "underline" in style and style["underline"]:
				style_str += " text-decoration:underline;"
			ret += f"{style_str}'>{strpart}</div>"

		return ret

	def add_docs(self, view, docregs, docstrs, sym_locs):
		flags = 128 # RegionFlags.HIDDEN
		for i in range(len(docstrs)):
			docstrs[i] = docstrs[i].replace("!href!", str(i))
		view.add_regions(key="hd_hover", regions=docregs, scope='', icon='', flags=flags, annotations=docstrs,
			             annotation_color='', on_navigate=lambda href: self.on_navigate(sym_loc=sym_locs[int(href)], is_hover=True))

	def find_def_and_comment(self, sym_loc, sym_name):
		""" For a given symbol, get the definition string and the comment string.

		Args:
		    sym_loc: The SymbolLocation for the symbol. Probably from find_symbol_definition(...)
		    sym_name: The string representing the name of the symbol.
		Returns:
		    v2: The temporary output panel into which the symbol is loaded.
		    def_reg: The region containing the definition of the symbol. If a function, then
		             this includes the parameters in the definition. Might be empty.
		    comment_reg: The region containing the comment immediately preceeding or following
		                 the symbol. Empty region if not found.
		"""
		# find the view for the given sym_loc, if already opened somewhere
		windows = [sublime.active_window()] + sublime.windows()
		for window in windows:
			v2 = window.find_open_file(sym_loc.path)
			if v2 != None:
				break

		# load in a new view for this unopened file
		if v2 == None:
			# find the syntax
			syntax = sublime.find_syntax_for_file(sym_loc.path)
			if syntax == None:
				print("no syntax")
				return sublime.Region(0,0)

			# create a hidden output panel with the surrounding 10 lines around the symbol
			sublime.active_window().destroy_output_panel("hd_output_panel")
			v2 = sublime.active_window().create_output_panel("hd_output_panel", True)
			start = max(0, sym_loc.row - 10)
			stop = sym_loc.row + 10
			pre_line, post_line, sym_reg = 0, 0, None
			with open(sym_loc.path, 'r') as f:
				lineno = 0
				for line in f:
					lineno += 1
					if lineno >= start:
						if lineno == sym_loc.row:
							sym_reg = sublime.Region(v2.size()+sym_loc.col-1, v2.size()+sym_loc.col+len(sym_name)-1)
						elif lineno < sym_loc.row:
							pre_line = v2.size()
						v2.run_command("hover_docs", args={ "mode": "append", "args": { "characters": line } })
						if lineno == sym_loc.row:
							post_line = v2.size()
					if lineno >= stop:
						break
				stop = lineno
			pre_line = v2.line(pre_line)
			post_line = v2.line(post_line)

			# set the syntax and let sublime parse the hidden output panel
			v2.assign_syntax(syntax)
		else:
			pos = self.get_pos(v2, sym_loc.row, sym_loc.col)
			sym_reg = sublime.Region(pos, pos+len(sym_name))
			pre_line = v2.line(pos-1)
			post_line = v2.line(v2.line(pos).b+1)

		# get the defstr from the symbol line
		# expand to the end of the function parameters
		sym_scopes = v2.scope_name(sym_reg.b).split(" ")
		sym_scopes = filter(lambda s: "parameters" in s, sym_scopes)
		sym_scopes = map(lambda s: s[:s.index("parameters")+10], sym_scopes)
		tmp_reg = self.expand_to_scope(v2, sym_reg.b, sym_scopes)
		sym_extracted_reg = sublime.Region(sym_reg.a, max(sym_reg.b, tmp_reg.b))
		def_reg = sym_extracted_reg

		# get the comment line(s), either above or below the symbol line
		comment_reg = sublime.Region(0,0)
		pre_line_str = v2.substr(pre_line).rstrip()
		post_line_str = v2.substr(post_line).rstrip()
		pnts = [pre_line.a+len(pre_line_str), post_line.a+len(post_line_str)]
		for pnt in pnts:
			comment_scope = v2.scope_name(pnt)
			if "comment" in comment_scope:
				comment_reg = v2.extract_scope(pnt)
				if comment_reg != None:
					# v2.sel().clear()
					# v2.sel().add(comment_reg)
					# v2.run_command("toggle_comment")
					# comment_reg = v2.sel()[0]
					break
		
		return v2, def_reg, comment_reg

	def expand_to_scope(self, view, point, matching_scopes):
		""" Finds the extent of the region that matches the given scopes.

		Args:
		    view: The view to search in
		    point: Where to start the region
		    matching_scopes: A list of strings that the scope names should start with
		Returns:
		    reg: The expanded region
		"""
		if type(matching_scopes) != list:
			matching_scopes = list(matching_scopes)
		beggining, ending = point, point

		# find the extent of the matching scopes
		for start, mod in [(point-1, -1), (point, 1)]:
			pos = start
			while pos > 0 and pos < view.size():
				scope_names = view.scope_name(pos).split(" ")
				scope_names = filter(lambda s: s != "", scope_names)
				found = False
				for scope_name in scope_names:
					for matching_scope in matching_scopes:
						if scope_name.startswith(matching_scope):
							found = True
							break
					if found:
						break
				if not found:
					break

				pos += mod

			if mod == -1:
				beggining = pos
			else:
				ending = pos

		reg = sublime.Region(beggining, ending)
		return reg

	def get_scope_spans(self, view, reg):
		""" Get the scope names for each character in a region.

		Args:
		    view: The containing view of the given region.
		    reg: The region to look in.
		Returns:
		    scope_spans: A list of [idx, len, scope_names].
		"""
		scope_spans = []

		last = []
		start = 0
		cnt = 1
		for i in range(reg.a, reg.a+reg.size()+1):
			scope_names = view.scope_name(i).split(" ")
			scope_names = list(filter(lambda s: s != "", scope_names))
			if i == reg.a:
				last = scope_names
				cnt = 1
			elif last == scope_names:
				cnt += 1
			else: # last != scope_names
				scope_spans.append([start, cnt, last])
				start = i - reg.a
				last = scope_names
				cnt = 1
		scope_spans.append([start, cnt, last])

		return scope_spans

	def get_pos(self, view, row, col):
		lines = view.lines(sublime.Region(0, view.size()))
		# print(f"lines[0-{len(lines)-1}], row: {row}, size: {view.size()}")
		line = lines[row-1]
		pos = line.a+col-1
		return pos

	def move_to(self, view, row, col):
		pos = self.get_pos(view, row, col)
		reg = sublime.Region(pos, pos)

		view.show_at_center(reg)
		view.sel().clear()
		view.sel().add(reg)

	def is_ctrl_pressed(self):
		# from uiautomation
		# https://github.com/yinkaisheng/Python-UIAutomation-for-Windows/blob/master/uiautomation/uiautomation.py
		try:
			import ctypes
			state = ctypes.windll.user32.GetAsyncKeyState(0x11)
			return bool(state & 0x8000)
		except Exception as e:
			pass
		return False

	def on_navigate(self, sym_loc, is_hover):
		is_same_file = sublime.active_window().active_view().file_name() == sym_loc.path
		if is_same_file and not self.is_ctrl_pressed():
			self.move_to(sublime.active_window().active_view(), sym_loc.row, sym_loc.col)
		else:
			flags = 1+16+32 # encoded position, semi-transient, add to selection
			v2 = sublime.active_window().open_file(f"{sym_loc.path}:{sym_loc.row}:{sym_loc.col}", flags=flags)
