
##################################################

'''
	bake_world_transform.py
	autor: Simon Bjork
	date: February 2021
	version: 2.0
	email: bjork.simon@gmail.com

	Rewrite of sb_bakeWorldPosition.py from 2013.

	Installation:

		- Add script to the Nuke plugin path.
		- Add (something like) the following to your menu.py

		import sb_bake_world_transform
		sb_tools = nuke.toolbar("Nodes").addMenu("sb tools", icon = "sb_tools.png" )

		# Non gui example.
		sb_tools.addCommand("sb BakeWorldPosition", 'sb_bake_world_transform.bake_world_transform()', '')

		# GUI example.
		sb_tools.addCommand("sb BakeWorldPosition (GUI)", 'sb_bake_world_transform.show_gui()', '')

'''
##################################################

import nuke
import nukescripts
import _nukemath
import math

##################################################

class RotationFilter:
	'''
	Filter rotations to avoid unexpected flipping between
	negative and positive values.

	Args:
		values (list): A list with xyz values expressed as a list/tuple.
		order (str): Rotation order.

	Info:

		Most of the code is written by Erwan Leroy.

		Based on a Blender addon by Manuel Odendahl.

		https://github.com/mapoga/nuke-vector-matrix/blob/21-add_euler_filters/rotation_filters.py
		https://gist.github.com/wesen/687d6fdf455cbc5da286		
		https://community.foundry.com/discuss/topic/152951/euler-filter
	
	'''
	def __init__(self, values, order="ZXY"):

		self.values = values
		self.order = order.lower()

	def split_axis_order(self):
		'''
		Converts a string 'XYZ' into a list of the corresponding indices: [0, 1, 2]
		
		Return: 
			A List of axis indices.
		'''
		axis_map = {'x': 0, 'y': 1, 'z': 2}
		return [axis_map[axis] for axis in self.order]

	def flip_euler(self, euler):		
		axis0, axis1, axis2 = self.split_axis_order()

		flipped = list(euler)
		flipped[axis0] += math.pi
		flipped[axis1] *= -1
		flipped[axis1] += math.pi
		flipped[axis2] += math.pi
		return flipped

	def euler_filter_1d(self, previous, current):
		'''
		Naively rotates the current angle until it's as close as possible to the previous angle.

		Args:
			previous (list): A list with xyz values expressed as a list/tuple.
			current (list): A list with xyz values expressed as a list/tuple.

		Return:
			Modified current angle towards previous angle.
		'''		
		while abs(previous - current) > math.pi:
			if previous < current:
				current -= 2 * math.pi
			else:
				current += 2 * math.pi

		return current

	def distance_squared(self, vec1, vec2):
		'''
		Calculate distance between two vector3 represented as lists of len 3.
		'''		
		return (vec1[0] - vec2[0])**2 + (vec1[1]  -vec2[1])**2 + (vec1[2] - vec2[2])**2

	def euler_filter_3d(self, previous, current):
		'''
		Attempts to minimize the amount of rotation between the current orientation and the previous one.
		Orientations are preserved, but amount of rotation is minimized.

		Args:
			previous: A list of xyz values expressed as a list/tuple.
			current: A list of xyz values expressed as a list/tuple.

		Return:
			list: A list with fixed values.

		'''
		# Start with a pass of Naive 1D filtering
		filtered = list(current)
		for axis in range(3):
			filtered[axis] = self.euler_filter_1d(previous[axis], filtered[axis])

		# Then flip the whole thing and do another pass of Naive filtering
		flipped = self.flip_euler(filtered)
		for axis in range(3):
			flipped[axis] = self.euler_filter_1d(previous[axis], flipped[axis])

		# Return the vector with the shortest distance from the target value.
		if self.distance_squared(filtered, previous) > self.distance_squared(flipped, previous):
			return flipped
		
		return filtered

	def filter(self):
		'''
		Run the rotation filter on the supplied list.

		Return:
			list: A list of fixed values.
		'''
		prev = None
		fixed = []

		# Often there's a flip between the first and second frame.
		# To get "nicer" start values, use the second value as the base value.
		if len(self.values) > 2:
			vals = self.values[1:]
		else:
			vals = self.values

		for i in vals:
			curr = [math.radians(x) for x in i]

			# If it's the first time, append the original value.			
			if not prev:
				prev = curr
				fixed.append(i)
				continue

			fix_radians = self.euler_filter_3d(prev, curr)
			prev = fix_radians

			# Convert to degrees AFTER we added the fixed radians to prev.
			fix = [math.degrees(x) for x in fix_radians]
			fixed.append(fix)

		# Fix the first frame.
		if len(self.values) > 2:
			first = self.euler_filter_3d([math.radians(x) for x in fixed[0]], [math.radians(y) for y in self.values[0]])
			first_deg = [math.degrees(x) for x in first]
			fixed.insert(0, first_deg)
		
		return fixed

def duplicate_node(node, inpanel=True):
	'''
	Duplicate a node including userKnobs.

	Args:
		node (obj): The node to be duplicated.

	Return:
		node
	'''
	node.setSelected(False)

	knobs = []
	for knob in node.writeKnobs(nuke.WRITE_USER_KNOB_DEFS | nuke.WRITE_NON_DEFAULT_ONLY | nuke.TO_SCRIPT).split("\n"):
		if knob.startswith(("translate", "rotate", "scaling", "useMatrix", "skew", "pivot", "uniform_scale", "rot_order")):
			continue
		knobs.append(knob)

	new_node = nuke.createNode(node.Class(), "\n".join(knobs), inpanel=inpanel)
	new_node.setSelected(False)
	new_node.setInput(0, None)
	new_node.setXYpos(node.xpos()+100, node.ypos())
	new_node.setName("{0}_BAKED".format(node.name().replace("_BAKED", "")))

	return new_node

def set_knob(knob, values, frames, cleanup=False):
	'''
	Set the knob value(s) using either knob.setValue or directly on the AnimCurve.

	Args:
		knob (obj): A knob object.
		values (list): A list of values.
		frames (list): A list of frame numbers to set knob values.
		cleanup (bool): Remove animation from knobs that are static.

	Return:
		None

	'''
	if not values or not frames:
		return

	# No animation.
	if len(values) == 1:
		knob.setValue(values[0])
		return

	if not len(values) == len(frames):
		print "Values and frames are not the same length"
		return

	anim_lists = {}

	for num1, value in enumerate(values):

		if not isinstance(value, (list, tuple)):
			value = [value]

		if len(value) > 1:
			try:
				knob.setSingleValue(False)
			except:
				pass

		# Generate a list with frame numbers and AnimationKey pairs.
		for num2, single in enumerate(value):

			if not num2 in anim_lists.keys():
				anim_lists[num2] = []
			
			anim_key = nuke.AnimationKey(frames[num1], single)
			anim_lists[num2].append(anim_key)

	# Set each value as animated.
	for i in anim_lists.keys():
		knob.setAnimated(i, True)

	anim_curves = knob.animations()

	# Set values.
	for i in anim_lists.keys():
		anim_curves[i].addKey(anim_lists[i])

	if cleanup:
		for i in anim_curves:
			if i.constant():
				knob.clearAnimated(i.knobIndex())	

def is_3d_node(node):
	'''
	Quick way to check if a node is a 3d node.

	Args:
		node (obj): A node object.

	Return:
		bool
	'''
	if "rot_order" in node.knobs():
		return True
	else:
		return False

def get_matrix(node, frame):
	'''
	Get the matrix from a given node.

	Args:
		node (obj): Node
		frame (int): Get matrix at the given frame.

	Return:
		Matrix4
	'''
	if "world_matrix" in node.knobs():
		matrix_list = node["world_matrix"].valueAt(frame)
	elif "matrix" in node.knobs():
		matrix_list = node["matrix"].valueAt(frame)
	else:
		return None

	matrix = _nukemath.Matrix4()

	for i in range(16):
		matrix[i] = matrix_list[i]

	# Convert matrix to column major.
	matrix.transpose()

	return matrix

def matrix_to_list(m):
	'''
	Convert a Matrix4 object
	to a list.

	Args:
		m (obj): A Matrix4 object.

	Return:
		list
	'''
	return [m[x] for x in range(16)]

def decompose_matrix(matrix, rot_order="ZXY"):
	'''
	Decompose a matrix to translate/rotate/scale values.

	Based on Ivan Busquets consolidateNodeTransforms function.
	http://community.foundry.com/discuss/topic/102234

	Args:
		matrix (obj): A Matrix4 object.
		rot_order (str): Rotation order as a string, i.e ZXY.

	Return:
		list: A list of translate, rotation and scale values.
	'''
	pos_matrix = _nukemath.Matrix4(matrix)
	pos_matrix.translationOnly()
	rot_matrix = _nukemath.Matrix4(matrix)
	rot_matrix.rotationOnly()
	scale_matrix = _nukemath.Matrix4(matrix)
	scale_matrix.scaleOnly()

	if rot_order == "XYZ":
		rot_radians = rot_matrix.rotationsXYZ()
	elif rot_order == "XZY":
		rot_radians = rot_matrix.rotationsXZY()
	elif rot_order == "YXZ":
		rot_radians = rot_matrix.rotationsYXZ()
	elif rot_order == "YZX":
		rot_radians = rot_matrix.rotationsYZX()
	elif rot_order == "ZXY":
		rot_radians = rot_matrix.rotationsZXY()
	elif rot_order == "ZYX":
		rot_radians = rot_matrix.rotationsZYX()
	else:
		rot_radians = rot_matrix.rotationsZXY()

	# Position
	position = [pos_matrix[12], pos_matrix[13], pos_matrix[14]]

	# Rotation in degrees.
	rotation = [math.degrees(rot_radians[0]), math.degrees(rot_radians[1]), math.degrees(rot_radians[2])]

	# Round scale values to avoid annoying 0.9999999.
	scale_dec = 6
	scale_raw = [scale_matrix.xAxis().x, scale_matrix.yAxis().y, scale_matrix.zAxis().z]	
	scale = [round(x, scale_dec) for x in scale_raw]

	return [position, rotation, scale]

def bake_world_transform(first=-1, last=-1, rot_order="current", filter_rotations=True, use_matrix=False):
	'''
	Bake world transform for each selected 3d nodes.

	Args:
		first (int): The first frame.
		last (int): The last frame.
		rot_order (str): Rotation order, i.e "ZXY", "XYZ" etc. If a value is not specified, use the current value.
		filter_rotations (bool): Apply a euler filter on the rotations to aviod flipping from positive to negative values.
		use_matrix (bool): Use the local matrix knob on the new node instead of decomposing the values to TRS.

	Return:
		node: The new node.

	'''	
	if first < 0:
		first = nuke.root().firstFrame()

	if last < first:
		last = nuke.root().lastFrame()

	# Create a list with all frames.
	frames = [x for x in range(first, last+1)]

	# Loop over all selected nodes and create a baked version.
	for node in nuke.selectedNodes():
		
		if not is_3d_node(node):
			continue

		translate = []
		rotate = []
		scale = []
		wm = []

		# Get the rotation order.
		rot_orders = node["rot_order"].values()
		if not rot_order in rot_orders:
			rot_order = node["rot_order"].value()

		# Loop over frames and get the world matrix.
		for frame in frames:
			matrix = get_matrix(node, frame)

			t,r,s = decompose_matrix(matrix, rot_order)

			translate.append(t)
			rotate.append(r)
			scale.append(s)
			
			matrix.transpose()
			matrix_list = matrix_to_list(matrix)
			wm.append(matrix_list)

		dup = duplicate_node(node)
		dup["rot_order"].setValue(rot_order)

		# Some transforms cant be decomposed to translate,rotate,scale.
		# For example, non uniform scaling and rotation can introduce a skew.
		if use_matrix:
			dup["useMatrix"].setValue(True)
			set_knob(dup["matrix"], wm, frames)
		else:
			for i in ("translate", "rotate", "scaling"):
				dup[i].setAnimated()

			if filter_rotations:
				rotate = RotationFilter(rotate, rot_order).filter()

			# Set the knob values.		
			set_knob(dup["translate"], translate, frames)
			set_knob(dup["rotate"], rotate, frames)
			set_knob(dup["scaling"], scale, frames)

class BakeWorldTransformPanel(nukescripts.PythonPanel):
	'''
	Call the bake command via a gui in order to customize for example frame range.
	'''
	def __init__(self):
		nukescripts.PythonPanel.__init__(self, "sb Bake World Position")
		self.ff = nuke.Int_Knob("ff", "first frame")
		self.lf = nuke.Int_Knob("lf", "last frame")
		self.div1 = nuke.Text_Knob("divider1", "")
		self.rot_order = nuke.Enumeration_Knob("rot_order", "rotation order", ["current", "XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"])
		self.euler_filter = nuke.Boolean_Knob("euler_filter", "euler filter")
		self.euler_filter.setFlag(nuke.STARTLINE)
		self.use_matrix = nuke.Boolean_Knob("use_matrix", "use matrix")
		self.use_matrix.setFlag(nuke.STARTLINE)
		self.div2 = nuke.Text_Knob("divider2", "")
		self.bake_btn = nuke.PyScript_Knob("bake", "Bake nodes")

		for i in [self.ff, self.lf, self.div1, self.rot_order, self.euler_filter, self.use_matrix, self.div2, self.bake_btn]:
			self.addKnob(i)

		self.ff.setValue(nuke.root().firstFrame())
		self.lf.setValue(nuke.root().lastFrame())
		self.euler_filter.setValue(True)

	def knobChanged(self, knob):		
		if knob is self.bake_btn:
			self.bake()

	def bake(self):
		'''
		Run the bake command.
		'''
		bake_world_position(
			self.ff.value(),
			self.lf.value(),
			self.rot_order.value(),
			self.euler_filter.value(),
			self.use_matrix.value()
		)

def show_gui():
	p = BakeWorldTransformPanel()
	p.show()