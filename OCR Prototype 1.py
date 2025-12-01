import cv2 # type: ignore
import pytesseract # type: ignore
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from PIL import Image, ImageTk # type: ignore
import numpy as np # type: ignore
import json
import os
import re
from pathlib import Path

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

class NumberOCRApp:
    def __init__(self, root):
        self.root = root
        self.root.title("16-Digit NIK Recognition OCR")
        self.root.geometry("1600x900")
        
        # Variables
        self.image_path = None
        self.original_image = None
        self.scale_factor = 1.0
        self.zoom_level = 1.0
        self.pan_x = 0
        self.pan_y = 0
        
        # Selection variables
        self.selection_mode = False
        self.start_x = None
        self.start_y = None
        self.rect_id = None
        self.selection_coords = None
        
        # Color picker variables
        self.color_picker_mode = False
        self.target_color = None
        self.color_tolerance = 40
        self.zoom_window = None
        
        # Preprocessing method
        self.preprocess_method = tk.StringVar(value="adaptive")
        
        # OCR method
        self.ocr_method = tk.StringVar(value="tesseract")
        
        # Auto-detection settings
        self.auto_detect = tk.BooleanVar(value=True)
        
        # Training data
        self.training_folder = "number_training_data"
        self.dataset_folder = "number_dataset"
        self.model_folder = "models"
        self.corrections = self.load_corrections()
        self.create_dataset_structure()
        
        # Digit boxes for manual correction
        self.digit_boxes = []
        self.last_processed_image = None
        self.last_raw_result = None
        
        # Create UI
        self.create_widgets()
    
    def auto_detect_nik_region(self, image):
        """Automatically detect NIK region in Indonesian ID card"""
        # Make a copy to work with
        img = image.copy()
        h, w = img.shape[:2]
        
        # Fixed NIK locations based on Indonesian ID card structure
        roi_top = int(h * 0.15)
        roi_bottom = int(h * 0.25)
        roi_left = int(w * 0.2)
        roi_right = int(w * 0.75)
        
        # Extract potential NIK region
        roi = img[roi_top:roi_bottom, roi_left:roi_right]
        
        if roi.size == 0:
            return None
        
        # Auto-detect text color from the region
        self.auto_detect_text_color(roi)
        
        # Convert to grayscale
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        
        # Apply preprocessing to enhance text
        processed = self.enhance_nik_region(gray)
        
        # Find contours
        contours, _ = cv2.findContours(processed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # Filter contours by size and aspect ratio
        potential_nik_contours = []
        for contour in contours:
            x, y, cw, ch = cv2.boundingRect(contour)
            area = cv2.contourArea(contour)
            
            # Look for contours that could contain NIK digits
            if (area > 100 and ch > 15 and cw > 100 and 
                ch/cw < 1.0 and ch/cw > 0.1):
                potential_nik_contours.append((x, y, cw, ch))
        
        if not potential_nik_contours:
            # Try alternative approach - look for text blocks
            return self.find_nik_by_text_structure(roi, roi_left, roi_top)
        
        # Sort by position and size
        potential_nik_contours.sort(key=lambda c: (c[1], c[0]))
        
        # Take the largest contour in the top area as potential NIK
        if potential_nik_contours:
            x, y, cw, ch = potential_nik_contours[0]
            
            # Expand the region slightly
            padding_x = 10
            padding_y = 5
            x = max(0, x - padding_x)
            y = max(0, y - padding_y)
            cw = min(roi.shape[1] - x, cw + 2 * padding_x)
            ch = min(roi.shape[0] - y, ch + 2 * padding_y)
            
            # Convert back to original image coordinates
            abs_x = roi_left + x
            abs_y = roi_top + y
            
            return (abs_x, abs_y, abs_x + cw, abs_y + ch)
        
        return None
    
    def auto_detect_text_color(self, roi):
        """Automatically detect text color from region and set tolerance"""
        if roi.size == 0:
            return
        
        # Convert to different color spaces for better analysis
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)
        
        # Calculate standard deviation to determine color variation
        std_bgr = np.std(roi, axis=(0, 1))
        avg_std = np.mean(std_bgr)
        
        # Auto-adjust tolerance based on color variation
        # More variation = higher tolerance
        self.color_tolerance = int(20 + avg_std * 0.5)
        self.color_tolerance = min(80, max(20, self.color_tolerance))
        
        # Update tolerance slider
        if hasattr(self, 'tolerance_slider'):
            self.tolerance_slider.set(self.color_tolerance)
            self.tolerance_label.config(text=str(self.color_tolerance))
        
        # Find dominant dark colors (likely text)
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        # Get pixels that are likely text (dark areas in binary image)
        text_mask = binary == 0
        if np.sum(text_mask) > 0:
            # Get average color of text regions
            text_pixels = roi[text_mask]
            if len(text_pixels) > 0:
                avg_color = np.median(text_pixels, axis=0)
                self.target_color = tuple(map(int, avg_color))
                
                # Update color display
                color_rgb = (self.target_color[2], self.target_color[1], self.target_color[0])
                hex_color = '#{:02x}{:02x}{:02x}'.format(*color_rgb)
                
                if hasattr(self, 'color_display'):
                    self.color_display.config(bg=hex_color)
                    self.color_label.config(text=f"RGB{color_rgb}")
                
                self.preprocess_method.set("color")
                self.status_label.config(text=f"✓ Auto-detected text color: RGB{color_rgb}, Tolerance: {self.color_tolerance}")
    
    def find_nik_by_text_structure(self, roi, roi_left, roi_top):
        """Find NIK by analyzing text structure and patterns"""
        # Use OCR to find text that matches NIK pattern
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        enhanced = self.enhance_nik_region(gray)
        
        # Try multiple OCR configurations
        configs = [
            r'--oem 3 --psm 6',
            r'--oem 3 --psm 7',
            r'--oem 3 --psm 8'
        ]
        
        for config in configs:
            try:
                text = pytesseract.image_to_string(enhanced, config=config)
                lines = text.split('\n')
                
                for i, line in enumerate(lines):
                    # Look for 16-digit pattern
                    digits_only = re.sub(r'[^0-9]', '', line)
                    if len(digits_only) == 16:
                        # Found potential NIK, try to locate its position
                        coords = self.locate_text_position(enhanced, line.strip())
                        if coords:
                            x, y, w, h = coords
                            abs_x = roi_left + x
                            abs_y = roi_top + y
                            return (abs_x, abs_y, abs_x + w, abs_y + h)
            except:
                continue
        
        return None
    
    def locate_text_position(self, image, text):
        """Locate the position of specific text in image"""
        try:
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            
            for i, detected_text in enumerate(data['text']):
                if text in detected_text.strip() and len(detected_text.strip()) > 10:
                    x = data['left'][i]
                    y = data['top'][i]
                    w = data['width'][i]
                    h = data['height'][i]
                    return (x, y, w, h)
        except:
            pass
        
        return None
    
    def enhance_nik_region(self, gray_image):
        """Enhance NIK region for better detection"""
        # Apply bilateral filter to reduce noise while keeping edges sharp
        denoised = cv2.bilateralFilter(gray_image, 9, 75, 75)
        
        # Apply CLAHE for contrast enhancement
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(denoised)
        
        # Apply adaptive threshold
        binary = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY, 11, 2)
        
        # Apply morphological operations to clean up
        kernel = np.ones((2, 2), np.uint8)
        cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        
        return cleaned

    def create_dataset_structure(self):
        """Create folder structure for digit dataset"""
        if not os.path.exists(self.dataset_folder):
            os.makedirs(self.dataset_folder)
        
        if not os.path.exists(self.training_folder):
            os.makedirs(self.training_folder)
        
        for digit in range(10):
            digit_folder = os.path.join(self.dataset_folder, str(digit))
            if not os.path.exists(digit_folder):
                os.makedirs(digit_folder)
    
    def load_corrections(self):
        """Load saved corrections"""
        correction_file = os.path.join(self.training_folder if hasattr(self, 'training_folder') else ".", "corrections.json")
        if os.path.exists(correction_file):
            try:
                with open(correction_file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_corrections(self):
        """Save corrections"""
        if not os.path.exists(self.training_folder):
            os.makedirs(self.training_folder)
        
        correction_file = os.path.join(self.training_folder, "corrections.json")
        try:
            with open(correction_file, 'w') as f:
                json.dump(self.corrections, f, indent=2)
        except Exception as e:
            print(f"Error saving corrections: {e}")
    
    def count_dataset_images(self):
        """Count dataset images"""
        count = 0
        if os.path.exists(self.dataset_folder):
            for root, dirs, files in os.walk(self.dataset_folder):
                count += len([f for f in files if f.endswith(('.png', '.jpg', '.jpeg'))])
        return count
        
    def create_widgets(self):
        control_frame = tk.Frame(self.root, bg="#2C3E50", pady=12)
        control_frame.pack(fill=tk.X)
        
        tk.Label(control_frame, text="16-Digit NIK Recognition OCR", 
                font=("Arial", 16, "bold"), bg="#2C3E50", fg="white").pack(side=tk.LEFT, padx=20)
        
        btn_frame = tk.Frame(control_frame, bg="#2C3E50")
        btn_frame.pack(side=tk.LEFT, padx=20)
        
        tk.Button(btn_frame, text="📂 Load Image", command=self.load_image, 
                 bg="#27AE60", fg="white", font=("Arial", 10, "bold"), 
                 padx=15, pady=8, cursor="hand2").pack(side=tk.LEFT, padx=3)
        
        tk.Button(btn_frame, text="🔍 Auto Detect", command=self.auto_detect_and_extract,
                 bg="#F39C12", fg="white", font=("Arial", 10, "bold"),
                 padx=15, pady=8, cursor="hand2").pack(side=tk.LEFT, padx=3)
        
        tk.Button(btn_frame, text="✂️ Select Area", command=self.toggle_selection,
                 bg="#8E44AD", fg="white", font=("Arial", 10, "bold"),
                 padx=15, pady=8, cursor="hand2").pack(side=tk.LEFT, padx=3)    
        
        tk.Button(btn_frame, text="🎨 Pick Color", command=self.toggle_color_picker,
                 bg="#3498DB", fg="white", font=("Arial", 10, "bold"),
                 padx=15, pady=8, cursor="hand2").pack(side=tk.LEFT, padx=3)
        
        tk.Button(btn_frame, text="🔢 Extract NIK", command=self.extract_numbers,
                 bg="#E67E22", fg="white", font=("Arial", 10, "bold"),
                 padx=15, pady=8, cursor="hand2").pack(side=tk.LEFT, padx=3)
        
        tk.Button(btn_frame, text="🗑️ Clear", command=self.clear_all,
                 bg="#E74C3C", fg="white", font=("Arial", 10, "bold"),
                 padx=15, pady=8, cursor="hand2").pack(side=tk.LEFT, padx=3)
        
        status_frame = tk.Frame(control_frame, bg="#2C3E50")
        status_frame.pack(side=tk.RIGHT, padx=20)
        
        dataset_count = self.count_dataset_images()
        self.dataset_label = tk.Label(status_frame, text=f"📊 Dataset: {dataset_count}", 
                font=("Arial", 9, "bold"), bg="#34495E", fg="white", 
                padx=10, pady=5)
        self.dataset_label.pack(side=tk.RIGHT, padx=5)
        
        settings_frame = tk.Frame(self.root, bg="#ECF0F1", pady=10)
        settings_frame.pack(fill=tk.X)
        
        tk.Label(settings_frame, text="OCR:", 
                font=("Arial", 10, "bold"), bg="#ECF0F1").pack(side=tk.LEFT, padx=(20, 5))
        
        ocr_frame = tk.Frame(settings_frame, bg="#ECF0F1")
        ocr_frame.pack(side=tk.LEFT, padx=5)
        
        ocr_methods = [
            ("Tesseract", "tesseract"),
        ]
        
        for text, value in ocr_methods:
            tk.Radiobutton(ocr_frame, text=text, variable=self.ocr_method, 
                          value=value, bg="#ECF0F1", font=("Arial", 9)).pack(side=tk.LEFT, padx=5)
        
        tk.Label(settings_frame, text="Preprocess:", 
                font=("Arial", 10, "bold"), bg="#ECF0F1").pack(side=tk.LEFT, padx=(20, 5))
        
        method_frame = tk.Frame(settings_frame, bg="#ECF0F1")
        method_frame.pack(side=tk.LEFT, padx=5)
        
        methods = [
            ("Adaptive", "adaptive"),
            ("Color", "color"),
            ("Edge", "edge"),
            ("Contrast", "contrast")
        ]
        
        for text, value in methods:
            tk.Radiobutton(method_frame, text=text, variable=self.preprocess_method, 
                          value=value, bg="#ECF0F1", font=("Arial", 9),
                          command=self.on_method_change).pack(side=tk.LEFT, padx=5)
        
        # Auto-detect checkbox
        auto_frame = tk.Frame(settings_frame, bg="#ECF0F1")
        auto_frame.pack(side=tk.LEFT, padx=20)
        
        tk.Checkbutton(auto_frame, text="Auto-detect NIK", variable=self.auto_detect,
                      bg="#ECF0F1", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        
        color_frame = tk.Frame(settings_frame, bg="#ECF0F1")
        color_frame.pack(side=tk.LEFT, padx=20)
        
        tk.Label(color_frame, text="Color:", 
                font=("Arial", 10, "bold"), bg="#ECF0F1").pack(side=tk.LEFT, padx=5)
        
        self.color_display = tk.Canvas(color_frame, width=40, height=25, 
                                       bg="black", highlightthickness=2, 
                                       highlightbackground="#34495E")
        self.color_display.pack(side=tk.LEFT, padx=5)
        
        self.color_label = tk.Label(color_frame, text="Black", 
                                    font=("Arial", 9), bg="#ECF0F1")
        self.color_label.pack(side=tk.LEFT, padx=5)
        
        tk.Button(color_frame, text="✏️", command=self.manual_color_input,
                 bg="#16A085", fg="white", font=("Arial", 9, "bold"),
                 padx=5, pady=2, cursor="hand2").pack(side=tk.LEFT, padx=2)
        
        tk.Label(settings_frame, text="Tol:", 
                font=("Arial", 10, "bold"), bg="#ECF0F1").pack(side=tk.LEFT, padx=(20, 5))
        
        self.tolerance_var = tk.IntVar(value=40)
        self.tolerance_slider = tk.Scale(settings_frame, from_=0, to=100, 
                                        orient=tk.HORIZONTAL, variable=self.tolerance_var,
                                        command=self.on_tolerance_change, length=150,
                                        bg="#ECF0F1")
        self.tolerance_slider.pack(side=tk.LEFT, padx=5)
        
        self.tolerance_label = tk.Label(settings_frame, text="40", 
                                       font=("Arial", 10, "bold"), bg="#ECF0F1", width=3)
        self.tolerance_label.pack(side=tk.LEFT, padx=5)
        
        self.status_label = tk.Label(settings_frame, text="Ready", 
                                     font=("Arial", 10), bg="#ECF0F1", fg="#2C3E50")
        self.status_label.pack(side=tk.RIGHT, padx=20)
        
        main_frame = tk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        canvas_frame = tk.Frame(main_frame, bg="#34495E", relief=tk.RIDGE, bd=2)
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        self.canvas = tk.Canvas(canvas_frame, bg="#2C3E50", cursor="cross")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.canvas.bind("<Motion>", self.on_mouse_move)
        
        right_frame = tk.Frame(main_frame, width=400)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH)
        right_frame.pack_propagate(False)
        
        canvas_container = tk.Frame(right_frame)
        canvas_container.pack(fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(canvas_container, orient="vertical")
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.right_canvas = tk.Canvas(canvas_container, yscrollcommand=scrollbar.set, bg="#ECF0F1")
        self.right_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar.config(command=self.right_canvas.yview)
        
        self.right_inner_frame = tk.Frame(self.right_canvas, bg="#ECF0F1")
        self.right_canvas_window = self.right_canvas.create_window((0, 0), window=self.right_inner_frame, anchor="nw")
        
        self.right_inner_frame.bind("<Configure>", lambda e: self.right_canvas.configure(scrollregion=self.right_canvas.bbox("all")))
        self.right_canvas.bind("<Configure>", lambda e: self.right_canvas.itemconfig(self.right_canvas_window, width=e.width))
        
        preview_label_frame = tk.LabelFrame(self.right_inner_frame, text="🔷 Processed Preview", 
                                           font=("Arial", 11, "bold"), bg="#ECF0F1",
                                           fg="#2C3E50", padx=10, pady=10)
        preview_label_frame.pack(fill=tk.X, pady=(10, 10), padx=10)
        
        self.processed_canvas = tk.Canvas(preview_label_frame, bg="white", 
                                          width=360, height=120,
                                          highlightthickness=1,
                                          highlightbackground="#95A5A6")
        self.processed_canvas.pack()
        
        original_label_frame = tk.LabelFrame(self.right_inner_frame, text="🖼️ Original Selection", 
                                            font=("Arial", 11, "bold"), bg="#ECF0F1",
                                            fg="#2C3E50", padx=10, pady=10)
        original_label_frame.pack(fill=tk.X, pady=(0, 10), padx=10)
        
        self.preview_canvas = tk.Canvas(original_label_frame, bg="white", 
                                        width=360, height=120,
                                        highlightthickness=1,
                                        highlightbackground="#95A5A6")
        self.preview_canvas.pack()
        
        results_frame = tk.LabelFrame(self.right_inner_frame, text="🆔 NIK Number", 
                                     font=("Arial", 12, "bold"), bg="#ECF0F1",
                                     fg="#2C3E50", padx=15, pady=15)
        results_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        result_display_frame = tk.Frame(results_frame, bg="white", relief=tk.SUNKEN, bd=2)
        result_display_frame.pack(fill=tk.X, pady=(0, 15))
        
        self.result_label = tk.Label(result_display_frame, text="", 
                                     font=("Courier New", 20, "bold"),
                                     bg="white", fg="#2C3E50", pady=15)
        self.result_label.pack()
        
        tk.Label(results_frame, text="Manual Correction (click to edit):", 
                font=("Arial", 10, "bold"), bg="#ECF0F1", fg="#34495E").pack(anchor=tk.W, pady=(5, 5))
        
        self.digit_entries = []
        for row in range(4):
            digit_row_frame = tk.Frame(results_frame, bg="#ECF0F1")
            digit_row_frame.pack(fill=tk.X, pady=2)
            
            for col in range(4):
                idx = row * 4 + col
                entry = tk.Entry(digit_row_frame, width=3, font=("Courier New", 16, "bold"),
                               justify=tk.CENTER, bg="white", fg="#2C3E50",
                               relief=tk.SOLID, bd=2)
                entry.pack(side=tk.LEFT, padx=3, pady=2)
                entry.bind('<KeyRelease>', lambda e, i=idx: self.on_digit_change(i))
                self.digit_entries.append(entry)
        
        action_frame = tk.Frame(results_frame, bg="#ECF0F1")
        action_frame.pack(fill=tk.X, pady=(15, 0))
        
        tk.Button(action_frame, text="✅ Save Correction", command=self.save_correction,
                 bg="#27AE60", fg="white", font=("Arial", 9, "bold"),
                 padx=15, pady=8, cursor="hand2").pack(fill=tk.X, pady=2)
        
        tk.Button(action_frame, text="📋 Copy", command=self.copy_result,
                 bg="#3498DB", fg="white", font=("Arial", 9, "bold"),
                 padx=15, pady=8, cursor="hand2").pack(fill=tk.X, pady=2)
        
        tk.Button(action_frame, text="💾 Save to Dataset", command=self.save_to_dataset,
                 bg="#9B59B6", fg="white", font=("Arial", 9, "bold"),
                 padx=15, pady=8, cursor="hand2").pack(fill=tk.X, pady=2)
        
        self.confidence_label = tk.Label(results_frame, text="", 
                                        font=("Arial", 9), bg="#ECF0F1", fg="#7F8C8D")
        self.confidence_label.pack(pady=(10, 0))
    
    def auto_detect_and_extract(self):
        """Automatically detect NIK region and extract numbers"""
        if self.original_image is None:
            messagebox.showwarning("Warning", "Please load an image first!")
            return
        
        self.status_label.config(text="🕵️ Auto-detecting NIK region...")
        self.root.update()
        
        # Auto-detect NIK region
        nik_region = self.auto_detect_nik_region(self.original_image)
        
        if nik_region:
            x1, y1, x2, y2 = nik_region
            self.selection_coords = nik_region
            
            # Draw selection rectangle on canvas
            self.draw_selection_rectangle(x1, y1, x2, y2)
            
            # Update preview
            roi = self.original_image[y1:y2, x1:x2]
            self.update_preview(roi)
            
            self.status_label.config(text="✓ NIK region auto-detected - Extracting numbers...")
            self.root.update()
            
            # Extract numbers
            self.extract_numbers()
        else:
            self.status_label.config(text="❌ Could not auto-detect NIK region. Please select manually.")
            messagebox.showwarning("Auto-detection Failed", 
                                 "Could not automatically detect NIK region. Please use manual selection.")
    
    def draw_selection_rectangle(self, x1, y1, x2, y2):
        """Draw selection rectangle on canvas"""
        # Convert to canvas coordinates
        canvas_x1 = x1 * self.scale_factor + self.image_offset_x
        canvas_y1 = y1 * self.scale_factor + self.image_offset_y
        canvas_x2 = x2 * self.scale_factor + self.image_offset_x
        canvas_y2 = y2 * self.scale_factor + self.image_offset_y
        
        # Remove existing rectangle
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        
        # Draw new rectangle
        self.rect_id = self.canvas.create_rectangle(
            canvas_x1, canvas_y1, canvas_x2, canvas_y2,
            outline="#00FF00", width=3, dash=(5, 5)
        )

    def on_method_change(self):
        """Handle preprocessing method change"""
        if self.selection_coords and self.original_image is not None:
            x1, y1, x2, y2 = self.selection_coords
            roi = self.original_image[y1:y2, x1:x2]
            self.update_preview(roi)
    
    def manual_color_input(self):
        """Manual color input dialog"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Manual Color Input")
        dialog.geometry("350x250")
        dialog.transient(self.root)
        dialog.grab_set()
        
        tk.Label(dialog, text="Enter RGB Color Values", font=("Arial", 12, "bold")).pack(pady=10)
        
        frame = tk.Frame(dialog)
        frame.pack(pady=10)
        
        tk.Label(frame, text="R:", font=("Arial", 10, "bold")).grid(row=0, column=0, padx=5, pady=5)
        r_entry = tk.Entry(frame, width=10, font=("Arial", 10))
        r_entry.grid(row=0, column=1, padx=5, pady=5)
        r_entry.insert(0, "0")
        
        tk.Label(frame, text="G:", font=("Arial", 10, "bold")).grid(row=1, column=0, padx=5, pady=5)
        g_entry = tk.Entry(frame, width=10, font=("Arial", 10))
        g_entry.grid(row=1, column=1, padx=5, pady=5)
        g_entry.insert(0, "0")
        
        tk.Label(frame, text="B:", font=("Arial", 10, "bold")).grid(row=2, column=0, padx=5, pady=5)
        b_entry = tk.Entry(frame, width=10, font=("Arial", 10))
        b_entry.grid(row=2, column=1, padx=5, pady=5)
        b_entry.insert(0, "0")
        
        preview_canvas = tk.Canvas(dialog, width=200, height=50, bg="black")
        preview_canvas.pack(pady=10)
        
        def update_preview(*args):
            try:
                r = max(0, min(255, int(r_entry.get())))
                g = max(0, min(255, int(g_entry.get())))
                b = max(0, min(255, int(b_entry.get())))
                hex_color = '#{:02x}{:02x}{:02x}'.format(r, g, b)
                preview_canvas.config(bg=hex_color)
            except:
                pass
        
        r_entry.bind('<KeyRelease>', update_preview)
        g_entry.bind('<KeyRelease>', update_preview)
        b_entry.bind('<KeyRelease>', update_preview)
        
        def apply_color():
            try:
                r = max(0, min(255, int(r_entry.get())))
                g = max(0, min(255, int(g_entry.get())))
                b = max(0, min(255, int(b_entry.get())))
                
                self.target_color = (b, g, r)
                
                hex_color = '#{:02x}{:02x}{:02x}'.format(r, g, b)
                self.color_display.config(bg=hex_color)
                self.color_label.config(text=f"RGB({r},{g},{b})")
                
                self.status_label.config(text=f"✓ Color set: RGB({r},{g},{b})")
                
                if self.selection_coords:
                    x1, y1, x2, y2 = self.selection_coords
                    roi = self.original_image[y1:y2, x1:x2]
                    self.update_preview(roi)
                
                dialog.destroy()
            except Exception as e:
                messagebox.showerror("Error", f"Invalid color values: {e}")
        
        tk.Button(dialog, text="Apply", command=apply_color, bg="#27AE60", fg="white",
                 font=("Arial", 10, "bold"), padx=30, pady=8).pack(pady=10)
    
    def on_tolerance_change(self, value):
        """Handle tolerance change"""
        self.color_tolerance = int(value)
        self.tolerance_label.config(text=str(int(value)))
        
        if self.selection_coords and self.original_image is not None:
            x1, y1, x2, y2 = self.selection_coords
            roi = self.original_image[y1:y2, x1:x2]
            self.update_preview(roi)
    
    def toggle_color_picker(self):
        """Toggle color picker mode"""
        if self.original_image is None:
            messagebox.showwarning("Warning", "Please load an image first!")
            return
        
        self.color_picker_mode = True
        self.selection_mode = False
        self.status_label.config(text="👆 Click on the NIK number to pick color")
        self.canvas.config(cursor="crosshair")
    
    def on_mouse_move(self, event):
        """Show zoom window when in color picker mode"""
        if not self.color_picker_mode or self.original_image is None:
            if self.zoom_window:
                self.zoom_window.destroy()
                self.zoom_window = None
            return
        
        x = int((event.x - self.image_offset_x) / self.scale_factor)
        y = int((event.y - self.image_offset_y) / self.scale_factor)
        
        h, w = self.original_image.shape[:2]
        if x < 0 or x >= w or y < 0 or y >= h:
            if self.zoom_window:
                self.zoom_window.destroy()
                self.zoom_window = None
            return
        
        if not self.zoom_window:
            self.zoom_window = tk.Toplevel(self.root)
            self.zoom_window.title("Color Picker Zoom")
            self.zoom_window.geometry("250x280+100+100")
            self.zoom_window.attributes('-topmost', True)
            self.zoom_window.overrideredirect(True)
            
            self.zoom_canvas = tk.Canvas(self.zoom_window, width=240, height=240, bg="white")
            self.zoom_canvas.pack(padx=5, pady=5)
            
            self.zoom_info = tk.Label(self.zoom_window, text="", font=("Arial", 9))
            self.zoom_info.pack()
        
        size = 20
        x1 = max(0, x - size // 2)
        y1 = max(0, y - size // 2)
        x2 = min(w, x1 + size)
        y2 = min(h, y1 + size)
        
        region = self.original_image[y1:y2, x1:x2].copy()
        
        center_y = y - y1
        center_x = x - x1
        cv2.line(region, (center_x, 0), (center_x, region.shape[0]), (0, 255, 0), 1)
        cv2.line(region, (0, center_y), (region.shape[1], center_y), (0, 255, 0), 1)
        
        zoomed = cv2.resize(region, (240, 240), interpolation=cv2.INTER_NEAREST)
        zoomed_rgb = cv2.cvtColor(zoomed, cv2.COLOR_BGR2RGB)
        
        zoom_img = Image.fromarray(zoomed_rgb)
        self.zoom_photo = ImageTk.PhotoImage(zoom_img)
        self.zoom_canvas.delete("all")
        self.zoom_canvas.create_image(120, 120, image=self.zoom_photo)
        
        color_bgr = self.original_image[y, x]
        rgb = (int(color_bgr[2]), int(color_bgr[1]), int(color_bgr[0]))
        self.zoom_info.config(text=f"RGB: {rgb}")
        
        self.zoom_window.geometry(f"+{event.x_root + 20}+{event.y_root + 20}")
    
    def pick_color(self, event):
        """Pick color from image"""
        if not self.color_picker_mode or self.original_image is None:
            return
        
        x = int((event.x - self.image_offset_x) / self.scale_factor)
        y = int((event.y - self.image_offset_y) / self.scale_factor)
        
        h, w = self.original_image.shape[:2]
        x = max(0, min(x, w-1))
        y = max(0, min(y, h-1))
        
        color_bgr = self.original_image[y, x]
        self.target_color = tuple(map(int, color_bgr))
        
        color_rgb = (self.target_color[2], self.target_color[1], self.target_color[0])
        hex_color = '#{:02x}{:02x}{:02x}'.format(*color_rgb)
        self.color_display.config(bg=hex_color)
        self.color_label.config(text=f"RGB{color_rgb}")
        
        self.preprocess_method.set("color")
        
        self.color_picker_mode = False
        self.canvas.config(cursor="cross")
        self.status_label.config(text=f"✓ Color picked: RGB{color_rgb}")
        
        if self.zoom_window:
            self.zoom_window.destroy()
            self.zoom_window = None
        
        if self.selection_coords:
            x1, y1, x2, y2 = self.selection_coords
            roi = self.original_image[y1:y2, x1:x2]
            self.update_preview(roi)
    
    def load_image(self):
        """Load image"""
        file_path = filedialog.askopenfilename(
            title="Select an ID Card Image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.tiff *.gif")]
        )
        
        if file_path:
            self.image_path = file_path
            self.original_image = cv2.imread(file_path)
            
            if self.original_image is None:
                messagebox.showerror("Error", "Failed to load image!")
                return
            
            self.clear_selection()
            self.display_image()
            
            # Auto-detect if enabled
            if self.auto_detect.get():
                self.auto_detect_and_extract()
            else:
                self.status_label.config(text="✓ Image loaded - Select the NIK number area")
    
    def display_image(self):
        """Display image on canvas"""
        if self.original_image is None:
            return
        
        image_rgb = cv2.cvtColor(self.original_image, cv2.COLOR_BGR2RGB)
        
        self.canvas.update()
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        
        if canvas_width <= 1 or canvas_height <= 1:
            canvas_width = 1000
            canvas_height = 600
        
        h, w = image_rgb.shape[:2]
        self.scale_factor = min(canvas_width/w, canvas_height/h, 1.0)
        new_w, new_h = int(w * self.scale_factor), int(h * self.scale_factor)
        
        resized = cv2.resize(image_rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
        pil_image = Image.fromarray(resized)
        self.photo = ImageTk.PhotoImage(pil_image)
        
        self.canvas.delete("all")
        self.canvas.create_image(canvas_width//2, canvas_height//2, 
                                image=self.photo, anchor=tk.CENTER)
        
        self.image_offset_x = (canvas_width - new_w) // 2
        self.image_offset_y = (canvas_height - new_h) // 2
    
    def update_preview(self, roi):
        """Update preview canvases"""
        if roi is None or roi.size == 0:
            return
        
        roi_rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
        h, w = roi_rgb.shape[:2]
        
        max_width = 360
        max_height = 120
        scale = min(max_width / w, max_height / h, 5.0)
        
        new_w, new_h = int(w * scale), int(h * scale)
        
        if new_w > 0 and new_h > 0:
            interp = cv2.INTER_LINEAR if scale > 1.0 else cv2.INTER_AREA
            preview_resized = cv2.resize(roi_rgb, (new_w, new_h), interpolation=interp)
            preview_pil = Image.fromarray(preview_resized)
            self.preview_photo = ImageTk.PhotoImage(preview_pil)
            
            self.preview_canvas.delete("all")
            self.preview_canvas.create_image(180, 60, image=self.preview_photo, anchor=tk.CENTER)
        
        processed = self.preprocess_for_numbers(roi)
        h_proc, w_proc = processed.shape[:2]
        
        scale = min(max_width / w_proc, max_height / h_proc, 3.0)
        new_w, new_h = int(w_proc * scale), int(h_proc * scale)
        
        if new_w > 0 and new_h > 0:
            interp = cv2.INTER_LINEAR if scale > 1.0 else cv2.INTER_AREA
            processed_resized = cv2.resize(processed, (new_w, new_h), interpolation=interp)
            
            if len(processed_resized.shape) == 2:
                processed_rgb = cv2.cvtColor(processed_resized, cv2.COLOR_GRAY2RGB)
            else:
                processed_rgb = processed_resized
            
            processed_pil = Image.fromarray(processed_rgb)
            self.processed_photo = ImageTk.PhotoImage(processed_pil)
            
            self.processed_canvas.delete("all")
            self.processed_canvas.create_image(180, 60, image=self.processed_photo, anchor=tk.CENTER)
    
    def toggle_selection(self):
        """Toggle selection mode"""
        if self.original_image is None:
            messagebox.showwarning("Warning", "Please load an image first!")
            return
        
        self.selection_mode = True
        self.color_picker_mode = False
        if self.zoom_window:
            self.zoom_window.destroy()
            self.zoom_window = None
        self.status_label.config(text="✂️ Click and drag to select the NIK number area")
    
    def on_mouse_down(self, event):
        """Mouse press handler"""
        if self.color_picker_mode:
            self.pick_color(event)
            return
        
        if not self.selection_mode or self.original_image is None:
            return
        
        self.start_x = event.x
        self.start_y = event.y
        
        if self.rect_id:
            self.canvas.delete(self.rect_id)
    
    def on_mouse_drag(self, event):
        """Mouse drag handler"""
        if self.color_picker_mode or not self.selection_mode or self.start_x is None:
            return
        
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        
        self.rect_id = self.canvas.create_rectangle(
            self.start_x, self.start_y, event.x, event.y,
            outline="#E74C3C", width=3
        )
    
    def on_mouse_up(self, event):
        """Mouse release handler"""
        if self.color_picker_mode or not self.selection_mode or self.start_x is None:
            return
        
        x1 = int((min(self.start_x, event.x) - self.image_offset_x) / self.scale_factor)
        y1 = int((min(self.start_y, event.y) - self.image_offset_y) / self.scale_factor)
        x2 = int((max(self.start_x, event.x) - self.image_offset_x) / self.scale_factor)
        y2 = int((max(self.start_y, event.y) - self.image_offset_y) / self.scale_factor)
        
        h, w = self.original_image.shape[:2]
        x1, y1 = max(0, min(x1, w)), max(0, min(y1, h))
        x2, y2 = max(0, min(x2, w)), max(0, min(y2, h))
        
        if x2 - x1 > 10 and y2 - y1 > 5:
            self.selection_coords = (x1, y1, x2, y2)
            self.status_label.config(text=f"✓ Selected: {x2-x1}x{y2-y1}px")
            
            roi = self.original_image[y1:y2, x1:x2]
            self.update_preview(roi)
        else:
            self.status_label.config(text="❌ Selection too small")
            if self.rect_id:
                self.canvas.delete(self.rect_id)
        
        self.selection_mode = False
    
    def clear_selection(self):
        """Clear selection"""
        self.selection_coords = None
        self.selection_mode = False
        self.color_picker_mode = False
        if self.rect_id:
            self.canvas.delete(self.rect_id)
            self.rect_id = None
        
        if self.zoom_window:
            self.zoom_window.destroy()
            self.zoom_window = None
        
        self.preview_canvas.delete("all")
        self.processed_canvas.delete("all")
        self.result_label.config(text="")
        self.confidence_label.config(text="")
        
        for entry in self.digit_entries:
            entry.delete(0, tk.END)
    
    def preprocess_for_numbers(self, image):
        """Advanced preprocessing for NIK number recognition"""
        method = self.preprocess_method.get()
        
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()
        
        scale = 5.0
        height, width = gray.shape
        gray = cv2.resize(gray, (int(width * scale), int(height * scale)), 
                         interpolation=cv2.INTER_CUBIC)
        
        if method == "color" and self.target_color is not None:
            scaled_img = cv2.resize(image, (int(image.shape[1] * scale), int(image.shape[0] * scale)), 
                                   interpolation=cv2.INTER_CUBIC)
            
            lower = np.array([max(0, self.target_color[i] - self.color_tolerance) for i in range(3)])
            upper = np.array([min(255, self.target_color[i] + self.color_tolerance) for i in range(3)])
            
            mask = cv2.inRange(scaled_img, lower, upper)
            result = np.ones_like(mask) * 255
            result[mask > 0] = 0
            
            kernel = np.ones((2,2), np.uint8)
            result = cv2.morphologyEx(result, cv2.MORPH_CLOSE, kernel, iterations=1)
            result = cv2.morphologyEx(result, cv2.MORPH_OPEN, kernel, iterations=1)
            
            return result
        
        elif method == "edge":
            denoised = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)
            edges = cv2.Canny(denoised, 50, 150)
            kernel = np.ones((3,3), np.uint8)
            dilated = cv2.dilate(edges, kernel, iterations=2)
            filled = cv2.morphologyEx(dilated, cv2.MORPH_CLOSE, kernel, iterations=2)
            return filled
        
        elif method == "contrast":
            denoised = cv2.fastNlMeansDenoising(gray, None, h=15, templateWindowSize=7, searchWindowSize=21)
            clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(4,4))
            enhanced = clahe.apply(denoised)
            kernel_sharpen = np.array([[-1,-1,-1], [-1, 9,-1], [-1,-1,-1]])
            sharpened = cv2.filter2D(enhanced, -1, kernel_sharpen)
            _, binary = cv2.threshold(sharpened, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            kernel = np.ones((2,2), np.uint8)
            cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
            return cleaned
        
        else:
            denoised = cv2.fastNlMeansDenoising(gray, None, h=12, templateWindowSize=7, searchWindowSize=21)
            bilateral = cv2.bilateralFilter(denoised, 9, 75, 75)
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
            enhanced = clahe.apply(bilateral)
            
            binary1 = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                           cv2.THRESH_BINARY, blockSize=15, C=10)
            binary2 = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                           cv2.THRESH_BINARY, blockSize=25, C=15)
            binary = cv2.bitwise_and(binary1, binary2)
            
            kernel_small = np.ones((2,2), np.uint8)
            kernel_medium = np.ones((3,3), np.uint8)
            opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel_small, iterations=1)
            closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel_medium, iterations=1)
            cleaned = cv2.medianBlur(closed, 3)
            
            return cleaned
    
    def extract_numbers(self):
        """Extract NIK numbers using selected OCR method"""
        if self.original_image is None:
            messagebox.showwarning("Warning", "Please load an image first!")
            return
        
        if self.selection_coords is None:
            # Try auto-detection if no selection
            if self.auto_detect.get():
                self.auto_detect_and_extract()
                return
            else:
                messagebox.showwarning("Warning", "Please select the NIK area first!")
                return
        
        try:
            self.extract_numbers_tesseract()
                
        except Exception as e:
            messagebox.showerror("Error", f"Extraction failed: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def extract_numbers_tesseract(self):
        """Extract using Tesseract OCR"""
        x1, y1, x2, y2 = self.selection_coords
        roi = self.original_image[y1:y2, x1:x2]
        
        self.last_processed_image = roi.copy()
        self.update_preview(roi)
        
        processed = self.preprocess_for_numbers(roi)
        
        configs = [
            r'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789',
            r'--oem 3 --psm 8 -c tessedit_char_whitelist=0123456789',
            r'--oem 3 --psm 13 -c tessedit_char_whitelist=0123456789',
        ]
        
        best_result = ""
        for config in configs:
            try:
                text = pytesseract.image_to_string(processed, config=config)
                numbers_only = re.sub(r'[^0-9]', '', text)
                if len(numbers_only) > len(best_result):
                    best_result = numbers_only
            except:
                continue
        
        self.display_result(best_result, "Tesseract")
    
    def display_result(self, result, method_name):
        """Display extraction result"""
        self.last_raw_result = result
        
        if len(result) >= 16:
            formatted = result[:16]
        elif len(result) > 0:
            formatted = result.ljust(16, '?')
        else:
            formatted = '?' * 16
        
        display_text = f"{formatted[0:4]} {formatted[4:8]} {formatted[8:12]} {formatted[12:16]}"
        self.result_label.config(text=display_text)
        
        for i, digit in enumerate(formatted):
            if i < 16:
                self.digit_entries[i].delete(0, tk.END)
                self.digit_entries[i].insert(0, digit)
        
        confidence = len([d for d in formatted if d != '?']) / 16 * 100
        self.confidence_label.config(text=f"{method_name} Confidence: {confidence:.0f}% ({len([d for d in formatted if d != '?'])}/16 digits)")
        
        if confidence == 100:
            self.status_label.config(text=f"✓ Success! All 16 digits extracted ({method_name})")
        else:
            self.status_label.config(text=f"⚠️ Extracted {len([d for d in formatted if d != '?'])}/16 digits ({method_name})")
    
    def on_digit_change(self, index):
        """Handle digit entry change"""
        digits = ''.join([entry.get()[:1] if entry.get() else '?' for entry in self.digit_entries])
        display_text = f"{digits[0:4]} {digits[4:8]} {digits[8:12]} {digits[12:16]}"
        self.result_label.config(text=display_text)
    
    def save_correction(self):
        """Save manual correction"""
        digits = ''.join([entry.get()[:1] if entry.get() else '?' for entry in self.digit_entries])
        
        if '?' in digits:
            messagebox.showwarning("Warning", "Please fill in all 16 digits before saving!")
            return
        
        if not re.match(r'^\d{16}$', digits):
            messagebox.showwarning("Warning", "NIK must be exactly 16 digits!")
            return
        
        if self.last_raw_result:
            self.corrections[self.last_raw_result] = digits
            self.save_corrections()
            messagebox.showinfo("Success", f"Correction saved!\n{self.last_raw_result} → {digits}")
            self.status_label.config(text=f"✓ Correction saved: {digits}")
    
    def copy_result(self):
        """Copy result to clipboard"""
        digits = ''.join([entry.get()[:1] if entry.get() else '?' for entry in self.digit_entries])
        
        if '?' in digits:
            response = messagebox.askyesno("Warning", 
                "Result contains unknown digits (?). Copy anyway?")
            if not response:
                return
        
        self.root.clipboard_clear()
        self.root.clipboard_append(digits)
        messagebox.showinfo("Copied", f"NIK copied to clipboard:\n{digits}")
        self.status_label.config(text="✓ Copied to clipboard")
    
    def save_to_dataset(self):
        """Save current digits to training dataset"""
        if self.selection_coords is None:
            messagebox.showwarning("Warning", "No selection to save!")
            return
        
        digit_images = self.get_digit_images()
        
        if len(digit_images) == 0:
            messagebox.showwarning("Warning", "No digits found to save!")
            return
        
        corrected_digits = ''.join([entry.get()[:1] if entry.get() else '' 
                                   for entry in self.digit_entries])
        
        if len(corrected_digits) != 16 or not corrected_digits.isdigit():
            messagebox.showwarning("Warning", 
                "Please ensure all 16 digits are correctly entered!")
            return
        
        saved_count = 0
        for i, digit_img in enumerate(digit_images[:16]):
            digit_label = corrected_digits[i]
            digit_folder = os.path.join(self.dataset_folder, digit_label)
            
            if not os.path.exists(digit_folder):
                os.makedirs(digit_folder)
            
            timestamp = int(os.times().elapsed * 1000)
            filename = f"digit_{timestamp}_{i}.png"
            filepath = os.path.join(digit_folder, filename)
            
            cv2.imwrite(filepath, digit_img)
            saved_count += 1
        
        new_count = self.count_dataset_images()
        self.dataset_label.config(text=f"📊 Dataset: {new_count}")
        
        messagebox.showinfo("Success", 
            f"Saved {saved_count} digit images to dataset!\n"
            f"Total dataset size: {new_count} images")
        self.status_label.config(text=f"✓ Saved {saved_count} digits to dataset")
    
    def get_digit_images(self):
        """Get segmented digit images"""
        if self.selection_coords is None:
            return []
        
        x1, y1, x2, y2 = self.selection_coords
        roi = self.original_image[y1:y2, x1:x2]
        processed = self.preprocess_for_numbers(roi)
        
        return self.segment_digits(processed)
    
    def segment_digits(self, processed_img):
        """Segment individual digits from processed image"""
        contours, _ = cv2.findContours(255 - processed_img, cv2.RETR_EXTERNAL, 
                                       cv2.CHAIN_APPROX_SIMPLE)
        
        if not contours:
            return []
        
        digit_contours = []
        h, w = processed_img.shape
        min_area = (h * w) * 0.001
        
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            area = cv2.contourArea(cnt)
            
            if area > min_area and 0.2 < ch/cw < 5:
                digit_contours.append((x, y, cw, ch))
        
        digit_contours.sort(key=lambda c: c[0])
        
        digits = []
        for x, y, cw, ch in digit_contours:
            pad = 2
            x1 = max(0, x - pad)
            y1 = max(0, y - pad)
            x2 = min(w, x + cw + pad)
            y2 = min(h, y + ch + pad)
            
            digit_img = processed_img[y1:y2, x1:x2]
            digits.append(digit_img)
        
        return digits
    
    def clear_all(self):
        """Clear everything"""
        self.clear_selection()
        self.status_label.config(text="Cleared - Ready for new image")

if __name__ == "__main__":
    root = tk.Tk()
    app = NumberOCRApp(root)
    root.mainloop()