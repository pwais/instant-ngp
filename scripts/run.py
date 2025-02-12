#!/usr/bin/env python3

# Copyright (c) 2020-2022, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.

import argparse
import os
import commentjson as json

import numpy as np

import sys
import time

from common import *
from scenes import scenes_nerf, scenes_image, scenes_sdf, scenes_volume, setup_colored_sdf

from tqdm import tqdm

import pyngp as ngp # noqa


def parse_args():
	parser = argparse.ArgumentParser(description="Run neural graphics primitives testbed with additional configuration & output options")

	parser.add_argument("--scene", "--training_data", default="", help="The scene to load. Can be the scene's name or a full path to the training data.")
	parser.add_argument("--mode", default="", const="nerf", nargs="?", choices=["nerf", "sdf", "image", "volume"], help="Mode can be 'nerf', 'sdf', or 'image' or 'volume'. Inferred from the scene if unspecified.")
	parser.add_argument("--network", default="", help="Path to the network config. Uses the scene's default if unspecified.")

	parser.add_argument("--load_snapshot", default="", help="Load this snapshot before training. recommended extension: .msgpack")
	parser.add_argument("--save_snapshot", default="", help="Save this snapshot after training. recommended extension: .msgpack")

	parser.add_argument("--nerf_compatibility", action="store_true", help="Matches parameters with original NeRF. Can cause slowness and worse results on some scenes.")
	parser.add_argument("--test_transforms", default="", help="Path to a nerf style transforms json from which we will compute PSNR")

	parser.add_argument("--screenshot_transforms", default="", help="Path to a nerf style transforms json from which to save a screenshot.")
	parser.add_argument("--screenshot_frames", nargs="*", help="Which frame(s) to take a screenshot of")
	parser.add_argument("--screenshot_dir", default="", help="which directory to output screenshots to")
	parser.add_argument("--screenshot_w", type=int, default=0, help="screenshot res width")
	parser.add_argument("--screenshot_h", type=int, default=0, help="screenshot res height")
	parser.add_argument("--screenshot_spp", type=int, default=16, help="screenshot spp")

	parser.add_argument("--gui", action="store_true", help="Show a gui.")
	parser.add_argument("--train", action="store_true", help="Train right from the beginning.")
	parser.add_argument("--n_steps", type=int, default=-1, help="Number of steps to train for before quitting.")

	parser.add_argument("--sharpen", default=0, help="Set amount of sharpening applied to NeRF training images")

	args = parser.parse_args()
	return args


if __name__ == "__main__":
	args = parse_args()

	if args.mode == "":
		if args.scene in scenes_sdf:
			args.mode = "sdf"
		elif args.scene in scenes_nerf:
			args.mode = "nerf"
		elif args.scene in scenes_image:
			args.mode = "image"
		elif args.scene in scenes_volume:
			args.mode = "volume"
		else:
			raise ValueError("Must specify either a valid '--mode' or '--scene' argument.")

	if args.mode == "sdf":
		mode = ngp.TestbedMode.Sdf
		configs_dir = os.path.join(ROOT_DIR, "configs", "sdf")
		scenes = scenes_sdf
	elif args.mode == "volume":
		mode = ngp.TestbedMode.Volume
		configs_dir = os.path.join(ROOT_DIR, "configs", "volume")
		scenes = scenes_volume
	elif args.mode == "nerf":
		mode = ngp.TestbedMode.Nerf
		configs_dir = os.path.join(ROOT_DIR, "configs", "nerf")
		scenes = scenes_nerf
	elif args.mode == "image":
		mode = ngp.TestbedMode.Image
		configs_dir = os.path.join(ROOT_DIR, "configs", "image")
		scenes = scenes_image

	base_network = os.path.join(configs_dir, "base.json")
	if args.scene in scenes:
		network = scenes[args.scene]["network"] if "network" in scenes[args.scene] else "base"
		base_network = os.path.join(configs_dir, network+".json")
	network = args.network if args.network else base_network
	if not os.path.isabs(network):
		network = os.path.join(configs_dir, network)


	testbed = ngp.Testbed(mode)
	testbed.nerf.sharpen = float(args.sharpen)

	if args.mode == "sdf":
		testbed.tonemap_curve = ngp.TonemapCurve.ACES

	if args.scene:
		scene=args.scene
		if not os.path.exists(args.scene) and args.scene in scenes:
			scene = os.path.join(scenes[args.scene]["data_dir"], scenes[args.scene]["dataset"])
		testbed.load_training_data(scene)

	if args.load_snapshot:
		print("loading snapshot ", args.load_snapshot)
		testbed.load_snapshot(args.load_snapshot)
	else:
		testbed.reload_network_from_file(network)

	ref_transforms = {}
	if args.screenshot_transforms: # try to load the given file straight away
		print("screenshot transforms from ", args.screenshot_transforms)
		with open(args.screenshot_transforms) as f:
			ref_transforms = json.load(f)

	if args.gui:
		sw=args.screenshot_w or 1920
		sh=args.screenshot_h or 1080
		while sw*sh > 1920*1080*4:
			sw = int(sw / 2)
			sh = int(sh / 2)
		testbed.init_window(sw, sh)

	testbed.shall_train = args.train if args.gui else True

	testbed.nerf.render_with_camera_distortion = True

	network_stem = os.path.splitext(os.path.basename(network))[0]
	if args.mode == "sdf":
		setup_colored_sdf(testbed, args.scene)

	if args.nerf_compatibility:
		# match nerf paper behaviour and train on a fixed white bg
		testbed.background_color = [1.0, 1.0, 1.0, 1.0]
		testbed.nerf.training.random_bg_color = False
		testbed.nerf.cone_angle_constant = 0
		print(f"NeRF compatibility mode enabled")

	old_training_step = 0
	n_steps = args.n_steps
	if n_steps < 0:
		n_steps = 100000

	if n_steps > 0:
		with tqdm(desc="Training", total=n_steps, unit="step") as t:
			while testbed.frame():
				if testbed.want_repl():
					repl(testbed)
				# What will happen when training is done?
				if testbed.training_step >= n_steps:
					if args.gui:
						testbed.shall_train = False
					else:
						break

				# Update progress bar
				if testbed.training_step < old_training_step or old_training_step == 0:
					old_training_step = 0
					t.reset()

				t.update(testbed.training_step - old_training_step)
				t.set_postfix(loss=testbed.loss)
				old_training_step = testbed.training_step

	if args.save_snapshot:
		print("saving snapshot ", args.save_snapshot)
		testbed.save_snapshot(args.save_snapshot, False)

	if args.test_transforms:
		print("test transforms from ", args.test_transforms)
		with open(args.test_transforms) as f:
			test_transforms = json.load(f)
		data_dir=os.path.dirname(args.test_transforms)
		totmse = 0
		totpsnr = 0
		totssim = 0
		totcount = 0
		minpsnr = 1000
		maxpsnr = 0

		spp = 8
		testbed.background_color = [0.0, 0.0, 0.0, 0.0]
		testbed.snap_to_pixel_centers = True
		testbed.nerf.rendering_min_alpha = 1e-4
		testbed.fov_axis = 0
		testbed.fov = test_transforms["camera_angle_x"] * 180 / np.pi
		testbed.shall_train = False
		with tqdm(list(enumerate(test_transforms["frames"])), unit="images", desc=f"Rendering test frame") as t:
			for i, frame in t:
				p = frame["file_path"]
				if "." not in p:
					p = p + ".png"
				ref_fname = os.path.join(data_dir, p)
				if not os.path.isfile(ref_fname):
					ref_fname = os.path.join(data_dir, p + ".png")
					if not os.path.isfile(ref_fname):
						ref_fname = os.path.join(data_dir, p + ".jpg")
						if not os.path.isfile(ref_fname):
							ref_fname = os.path.join(data_dir, p + ".jpeg")
							if not os.path.isfile(ref_fname):
								ref_fname = os.path.join(data_dir, p + ".exr")
				ref_image = read_image(ref_fname)
				ref_image += (1.0 - ref_image[...,3:4]) # composite ref on opaque white in linear land
				if i == 0:
					write_image("ref.png", ref_image)

				testbed.set_nerf_camera_matrix(np.matrix(frame["transform_matrix"])[:-1,:])
				image = testbed.render(ref_image.shape[1], ref_image.shape[0], spp, True)
				image[...,:3] = linear_to_srgb(image[...,:3])
				image += (1.0 - image[...,3:4]) # composite on opaque white in SRGB land
				image[...,:3] = srgb_to_linear(image[...,:3])
				if i == 0:
					write_image("out.png", image)

				diffimg = np.absolute(image - ref_image)
				diffimg[...,3:4] = 1.0
				if i == 0:
					write_image("diff.png", diffimg)

				A = linear_to_srgb(image[...,:3])
				R = linear_to_srgb(ref_image[...,:3])
				mse = float(compute_error("MSE",A,R))
				ssim = 0 # float(compute_error("SSIM",A,R))
				totssim += ssim
				totmse += mse
				psnr = mse2psnr(mse)
				totpsnr += psnr
				minpsnr = psnr if psnr<minpsnr else minpsnr
				maxpsnr = psnr if psnr>maxpsnr else maxpsnr
				totcount = totcount+1
				t.set_postfix(psnr = totpsnr/(totcount or 1))


		psnr_avgmse = mse2psnr(totmse/(totcount or 1))
		psnr = totpsnr/(totcount or 1)
		ssim = totssim/(totcount or 1)
		print(f"psnr {psnr} average, psnr range {minpsnr}-{maxpsnr}, ssim {ssim}")

	if args.screenshot_w:
		if ref_transforms:
			testbed.fov_axis = 0
			testbed.fov = ref_transforms["camera_angle_x"] * 180 / np.pi
			if not len(args.screenshot_frames):
				args.screenshot_frames = range(len(ref_transforms))
			print(args.screenshot_frames)
			for idx in args.screenshot_frames:
				f = ref_transforms["frames"][int(idx)]
				print(f)
				cam_matrix = f["transform_matrix"]
				testbed.set_nerf_camera_matrix(np.matrix(cam_matrix)[:-1,:])
				outname = os.path.join(args.screenshot_dir, os.path.basename(f["file_path"]))
				print(f"rendering {outname}")
				image = testbed.render(args.screenshot_w or int(ref_transforms["w"]), args.screenshot_h or int(ref_transforms["h"]), args.screenshot_spp, True)
				os.makedirs(os.path.dirname(outname), exist_ok=True)
				write_image(outname, image)
		else:
			outname = os.path.join(args.screenshot_dir, args.scene + "_" + network_stem)
			print(f"rendering {outname}.png")
			image = testbed.render(args.screenshot_w, args.screenshot_h, args.screenshot_spp, True)
			if os.path.dirname(outname) != "":
				os.makedirs(os.path.dirname(outname), exist_ok=True)
			write_image(outname + ".png", image)



